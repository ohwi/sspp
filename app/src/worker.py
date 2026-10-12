import time
import logging
import cv2
import mss
from mss.exception import ScreenShotError
import numpy as np
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal
from typing import Dict, Any, Tuple, Optional

# 모듈 임포트
from src.load_image import load_templates
from src.load_build import BuildLoader
from src.load_resolution import get_game_geometry, get_capture_area
from src.card_matcher import match_card
from src.badge_model import load_badge_model

try:
    from config import TEMPLATES_DIR, RESOURCES_DIR, ROIS, FACE_OFFSET, REFERENCE_WIDTH, AppStatus
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config import TEMPLATES_DIR, RESOURCES_DIR, ROIS, FACE_OFFSET, REFERENCE_WIDTH, AppStatus

logger = logging.getLogger("Worker")


def _capture_image(sct, area: dict, scale_factor: float) -> Optional[np.ndarray]:
    """음수 화면 좌표(보조 모니터)는 유효하며 빈 캡처만 거부합니다."""
    if (area["width"] <= 0 or area["height"] <= 0
            or not np.isfinite(scale_factor) or scale_factor <= 0):
        return None
    try:
        image = np.asarray(sct.grab(area))
        if image.ndim not in (2, 3) or not image.size:
            return None
        if scale_factor != 1.0:
            width = max(1, round(image.shape[1] * scale_factor))
            height = max(1, round(image.shape[0] * scale_factor))
            interpolation = cv2.INTER_AREA if scale_factor < 1 else cv2.INTER_LINEAR
            image = cv2.resize(image, (width, height), interpolation=interpolation)
        return image
    except (cv2.error, ScreenShotError, ValueError, TypeError, RuntimeError, OSError):
        return None


def _capture_gray(sct, area: dict, scale_factor: float) -> Optional[np.ndarray]:
    image = _capture_image(sct, area, scale_factor)
    if image is None or image.ndim == 2:
        return image
    try:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    except cv2.error:
        return None


def _match_score(image: np.ndarray, template: np.ndarray, method: int,
                 mask: Optional[np.ndarray] = None) -> Optional[float]:
    """호환되지 않는 템플릿과 OpenCV의 NaN/Inf 결과를 후보에서 제외합니다."""
    if (image.ndim != 2 or template.ndim != 2 or not template.size
            or template.shape[0] > image.shape[0]
            or template.shape[1] > image.shape[1]
            or template.dtype != image.dtype):
        return None
    if mask is not None and (mask.shape != template.shape or not np.any(mask)):
        return None
    # 일정한 스킬 템플릿은 CCOEFF_NORMED에서 모든 화면에 1.0을 반환합니다.
    if method == cv2.TM_CCOEFF_NORMED and np.ptp(template) == 0:
        return None
    try:
        scores = cv2.matchTemplate(image, template, method, mask=mask)
    except cv2.error:
        return None
    scores = scores[np.isfinite(scores)]
    if method == cv2.TM_SQDIFF_NORMED:
        scores = scores[scores >= -1e-6]
        return max(0.0, float(scores.min())) if scores.size else None
    scores = scores[(scores >= -1.000001) & (scores <= 1.000001)]
    return float(np.clip(scores.max(), -1.0, 1.0)) if scores.size else None

class MatcherWorker(QThread):
    # --- Signals ---
    # 매칭 결과: (index, text, score, matched, priority, target_level)
    match_signal = pyqtSignal(int, str, float, bool, int, object)
    # 초기화 신호
    reset_signal = pyqtSignal()
    # 상태 메시지: (StatusEnum, Message)
    status_signal = pyqtSignal(AppStatus, str)
    # 디버그 정보: (index, text, score)
    debug_signal = pyqtSignal(int, str, float)
    # 로딩 완료 신호
    initial_load_finished = pyqtSignal()
    
    geometry_signal = pyqtSignal(dict)

    def __init__(self, build_file: str):
        super().__init__()
        self.build_file = build_file
        self.build_loader: Optional[BuildLoader] = None
        self.running = True
        self.paused = True
        self.build_error: Optional[str] = None
        self.badge_model = load_badge_model(Path(RESOURCES_DIR) / "models")

    def update_build(self, new_build_file: str):
        """빌드 파일이 변경되었을 때 호출"""
        self.build_file = new_build_file
        try:
            self.build_loader = BuildLoader(self.build_file)
            self.build_error = None
            self.reset_signal.emit()
            logger.info(f"빌드 파일 변경됨: {new_build_file}")
            self.status_signal.emit(AppStatus.IDLE, f"빌드 변경됨: {new_build_file}")
        except Exception as e:
            self.build_loader = None
            self.build_error = f"빌드 로드 실패: {e}"
            self.paused = True
            self.reset_signal.emit()
            logger.error(self.build_error)
            self.status_signal.emit(AppStatus.ERROR, self.build_error)

    def set_paused(self, paused: bool):
        """일시정지 상태 제어"""
        if not paused and self.build_loader is None:
            self.paused = True
            self.reset_signal.emit()
            self.status_signal.emit(AppStatus.ERROR, self.build_error or "빌드 파일을 선택해주세요")
            return False
        self.paused = paused
        if self.paused:
            status = AppStatus.ERROR if self.build_error else AppStatus.PAUSED
            self.status_signal.emit(status, self.build_error or "일시정지됨")
            self.reset_signal.emit()
            logger.debug("매칭 일시정지")
        else:
            self.status_signal.emit(AppStatus.RUNNING, "실행중")
            logger.debug("매칭 재개")
        return True

    def run(self):
        """메인 실행 루프"""
        logger.info("Worker 스레드 시작")
        self.status_signal.emit(AppStatus.LOADING, "리소스 로딩 중...")
        
        # 템플릿 실패는 종료하지만, 잘못된 빌드는 선택을 바꿔 복구할 수 있습니다.
        try:
            face_templates, skill_templates = load_templates(TEMPLATES_DIR)
        except Exception as e:
            logger.critical(f"초기화 중 치명적 오류: {e}")
            self.status_signal.emit(AppStatus.ERROR, f"초기화 오류: {e}")
            return

        if not face_templates:
            logger.error("얼굴 템플릿 로드 실패 (데이터 없음)")
            self.status_signal.emit(AppStatus.ERROR, "오류: 템플릿 로드 실패")
            return
        
        logger.info(f"리소스 로드 완료 (Face: {len(face_templates)}, Skill: {len(skill_templates)})")
        self.update_build(self.build_file)
        if self.build_loader is not None:
            self.initial_load_finished.emit()

        # 2. 감시 루프 시작
        with mss.mss() as sct:
            while self.running:
                if self.paused or self.build_loader is None:
                    time.sleep(0.5)
                    continue

                try:
                    # 게임 창 위치 찾기
                    geo = get_game_geometry()
                    
                    if not geo:
                        self.status_signal.emit(AppStatus.IDLE, "게임 찾는 중...")
                        self.reset_signal.emit()
                        time.sleep(1)
                        continue

                    # [★핵심 수정 유지] 찾은 좌표를 오버레이로 전송
                    self.geometry_signal.emit(geo) 
                    self.status_signal.emit(AppStatus.RUNNING, "실행중")

                    # 화면 스캔 및 인식 처리
                    self.process_rois(sct, geo, face_templates, skill_templates)

                except Exception as e:
                    logger.error(f"메인 루프 에러: {e}")
                    self.status_signal.emit(AppStatus.ERROR, "시스템 오류 발생")
                    time.sleep(1)

                time.sleep(0.1)

    def process_rois(self, sct, geo: dict, face_templates: dict, skill_templates: dict):
        """모든 ROI(감시 영역)를 순회하며 인식 수행"""
        if geo["w"] <= 0 or geo["h"] <= 0:
            return
        scale_factor = REFERENCE_WIDTH / geo["w"]

        for i, roi in enumerate(ROIS):
            # 1단계: 얼굴 인식 시도
            detected_char, diff = self.detect_face(sct, geo, roi, face_templates, scale_factor)
            
            # 디버그 정보 전송
            debug_text = f"[FACE]{detected_char}" if detected_char else "No Face"
            self.debug_signal.emit(i, debug_text, 1.0 - diff)

            # 매칭 임계값 (0.15 diff = 85% 일치)
            if detected_char and diff <= 0.15:
                # 얼굴을 찾았으면 -> 2단계: 스킬 인식 시도
                if detected_char not in skill_templates:
                    # 스킬 템플릿이 없는 캐릭터인 경우 (얼굴만 매칭하고 종료)
                    self.match_signal.emit(i, f"{detected_char}", 1.0 - diff, True, 0, None)
                    continue

                self.detect_skill(sct, geo, roi, detected_char, skill_templates, scale_factor, i)
            else:
                # 얼굴 인식 실패 시 초기화
                self.match_signal.emit(i, "", 0.0, False, 0, None)

    def detect_face(self, sct, geo: dict, roi: dict, face_templates: dict, scale_factor: float) -> Tuple[Optional[str], float]:
        """얼굴 인식 로직"""
        face_area = get_capture_area(geo, roi, FACE_OFFSET)
        
        gray = _capture_gray(sct, face_area, scale_factor)
        # SQDIFF alone can score a plain white frame as a bright portrait.
        if gray is None or np.ptp(gray) < 5:
            return None, 1.0

        detected_char = None
        best_diff = 1.0
        second_diff = 1.0
        loader = self.build_loader
        allowed = (loader.get_recognition_characters()
                   if loader and hasattr(loader, "get_recognition_characters") else None)
        
        # 템플릿 매칭 수행
        for char_name, (face_img, face_mask) in face_templates.items():
            if allowed is not None and char_name not in allowed:
                continue
            # 마스크를 사용한 템플릿 매칭 (SQDIFF_NORMED: 0에 가까울수록 일치)
            min_val = _match_score(gray, face_img, cv2.TM_SQDIFF_NORMED, face_mask)

            if min_val is not None and min_val < best_diff:
                second_diff = best_diff
                best_diff = min_val
                detected_char = char_name
            elif min_val is not None:
                second_diff = min(second_diff, min_val)

        if second_diff - best_diff <= 1e-6:
            return None, best_diff
                
        return detected_char, best_diff

    def detect_skill(self, sct, geo: dict, roi: dict, char_name: str, skill_templates: dict, scale_factor: float, index: int):
        """스킬 아이콘 인식 로직"""
        card_area = get_capture_area(geo, roi, None)
        
        candidates = skill_templates.get(char_name, {})
        # Preserve the gray legacy path; calibrated candidates require color evidence.
        calibrated = any(isinstance(value, dict) for value in candidates.values())
        image = (_capture_image(sct, card_area, scale_factor) if calibrated
                 else _capture_gray(sct, card_area, scale_factor))
        if image is None:
            self.match_signal.emit(index, "", 0.0, False, 0, None)
            return

        build_loader = self.build_loader
        allowed = (build_loader.get_recognition_potentials(char_name)
                   if build_loader and hasattr(build_loader, "get_recognition_potentials") else None)
        best_filename, best_score, diagnostics = match_card(
            image, char_name, skill_templates, allowed_ids=allowed, badge_model=self.badge_model)
        self.debug_signal.emit(index, best_filename or diagnostics.get("reason", "Unknown"), best_score)

        # 스킬 매칭 임계값 (75% 이상)
        if best_filename and best_score >= 0.75:
            # 빌드 로더에서 우선순위 가져오기
            priority = 0
            target_level = None
            if build_loader:
                priority = build_loader.get_priority(char_name, best_filename)
                target_level = build_loader.get_target_level(char_name, best_filename)

            self.match_signal.emit(index, best_filename, best_score, True, priority, target_level)
        else:
            # 얼굴은 맞는데 스킬을 못 찾은 경우 (캐릭터 이름만 표시)
            self.match_signal.emit(index, f"{char_name} (?)", 0.0, True, 0, None)

    def stop(self):
        """스레드 안전 종료"""
        self.running = False
        self.quit()
        self.wait(2000)
