import ctypes
import logging
import math
from ctypes import wintypes
from typing import Optional, Dict, Any, Union

import pygetwindow as gw

# ------------------------------------------------------
# 설정 임포트
# ------------------------------------------------------
try:
    from config import TARGET_GAME_TITLE, REFERENCE_WIDTH, REFERENCE_HEIGHT
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from config import TARGET_GAME_TITLE, REFERENCE_WIDTH, REFERENCE_HEIGHT

# 로거 설정
logger = logging.getLogger("Resolution")

# ------------------------------------------------------
# WinAPI 구조체 및 상수 (Internal)
# ------------------------------------------------------
class _RECT(ctypes.Structure):
    """Windows API용 RECT 구조체"""
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

# 중복 로그 방지를 위한 전역 변수
_last_log_msg: Optional[str] = None

def _log_once(msg: str, level: int = logging.DEBUG):
    """
    이전과 동일한 메시지는 출력하지 않는 로깅 함수.
    반복 루프에서 로그 스팸을 방지하기 위해 사용합니다.
    """
    global _last_log_msg
    if msg != _last_log_msg:
        logger.log(level, msg)
        _last_log_msg = msg

# ------------------------------------------------------
# Core Functions
# ------------------------------------------------------
def get_game_geometry() -> Optional[Dict[str, int]]:
    """
    현재 게임 창의 위치와 크기 정보를 가져옵니다.
    
    Returns:
        성공 시: {"x": int, "y": int, "w": int, "h": int}
        실패 시: None
    """
    try:
        # 1. 윈도우 핸들 찾기
        windows = gw.getWindowsWithTitle(TARGET_GAME_TITLE)
        if not windows:
            _log_once(f"창을 찾을 수 없음. 검색어: '{TARGET_GAME_TITLE}'", logging.DEBUG)
            return None
        
        # 같은 검색어를 포함한 다른 창보다 게임의 정확한 제목을 우선합니다.
        target_window = next((window for window in windows
                              if window.title.casefold() == TARGET_GAME_TITLE.casefold()), windows[0])
        hwnd = target_window._hWnd
        
        # 2. 최소화 여부 확인
        if ctypes.windll.user32.IsIconic(hwnd):
            _log_once("게임 창이 최소화되어 있습니다.", logging.DEBUG)
            return None
        
        # 3. 내부 크기 (Client Area) 계산
        #    GetWindowRect는 테두리 포함이므로 GetClientRect 사용
        client_rect = _RECT()
        if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            _log_once("게임 클라이언트 영역을 읽을 수 없습니다.", logging.WARNING)
            return None
        width = client_rect.right - client_rect.left
        height = client_rect.bottom - client_rect.top

        # 4. 화면 상 절대 위치 (Client Origin -> Screen Coordinate)
        point = wintypes.POINT(0, 0)
        if not ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point)):
            _log_once("게임 클라이언트 위치를 읽을 수 없습니다.", logging.WARNING)
            return None
        
        if width <= 0 or height <= 0:
            _log_once("게임 창 크기가 유효하지 않습니다 (0x0).", logging.WARNING)
            return None

        # 성공 시 로그 (최초 1회만)
        _log_once(f"게임 발견 성공! 위치: {point.x},{point.y} 크기: {width}x{height}", logging.INFO)

        return {
            "x": int(point.x), 
            "y": int(point.y), # 창의 클라이언트 영역 좌상단 절대 좌표
            "w": int(width),   
            "h": int(height)   # 창의 내부 크기
        }
        
    except Exception as e:
        _log_once(f"get_game_geometry 내부 오류: {e}", logging.ERROR)
        return None

def get_capture_area(
    geo: Dict[str, int], 
    roi_ratio: Dict[str, float], 
    face_offset_ratio: Optional[Dict[str, float]] = None
) -> Dict[str, int]:
    """
    해상도 정보(geo)와 비율(roi_ratio)을 받아 mss 캡처용 절대 좌표를 계산합니다.
    
    Args:
        geo: get_game_geometry()의 리턴값 (x, y, w, h)
        roi_ratio: config.ROIS에 정의된 비율 정보 (x, y, w, h)
        face_offset_ratio: (선택) 얼굴 영역 오프셋 비율 정보
        
    Returns:
        mss 포맷의 영역 정보: {"top": int, "left": int, "width": int, "height": int}
    """
    # 1. ROI 기본 영역 계산 (상대 좌표)
    roi_x = int(geo["w"] * roi_ratio["x"])
    roi_y = int(geo["h"] * roi_ratio["y"])
    
    final_x: int
    final_y: int
    final_w: int
    final_h: int

    # 2. 오프셋 적용 여부 분기
    if face_offset_ratio:
        # 얼굴 영역 계산 (ROI 기준 상대 위치 적용)
        offset_x = int(geo["w"] * face_offset_ratio["x"])
        offset_y = int(geo["h"] * face_offset_ratio["y"])
        offset_w = int(geo["w"] * face_offset_ratio["w"])
        offset_h = int(geo["h"] * face_offset_ratio["h"])
        
        final_x = roi_x + offset_x
        final_y = roi_y + offset_y
        final_w = offset_w
        final_h = offset_h
    else:
        # 일반 카드 영역 (ROI 자체 크기)
        final_x = roi_x
        final_y = roi_y
        final_w = int(geo["w"] * roi_ratio["w"])
        final_h = int(geo["h"] * roi_ratio["h"])

    # 3. 절대 좌표 변환 (창 위치 + 상대 좌표)
    return {
        "top": geo["y"] + final_y,
        "left": geo["x"] + final_x,
        "width": final_w,
        "height": final_h
    }


def get_reference_scale(geo: Dict[str, int]) -> float:
    """Normalize observed content pixels, independently of the desktop DPI value."""
    width, height = geo.get("w"), geo.get("h")
    if (not isinstance(width, (int, float)) or not isinstance(height, (int, float))
            or not math.isfinite(width) or not math.isfinite(height)
            or width <= 0 or height <= 0):
        raise ValueError("Content geometry must have positive finite dimensions")
    return REFERENCE_WIDTH / width


def resolve_content_geometry(
    geo: Dict[str, int], observed_image=None
) -> tuple[Dict[str, int], Dict[str, Any]]:
    """Use observed symmetric black bars to locate a centered reference viewport.

    Aspect ratio alone never changes the geometry. The optional image must be
    the complete client image in the same physical pixels as ``geo``. The caller
    can cache this observation while window geometry remains unchanged. This
    function performs no capture, DPI changes, or game interaction.
    """
    original = {key: geo[key] for key in ("x", "y", "w", "h")}
    diagnostics = {"applied": False, "reason": "no_observation"}
    if (any(type(original[key]) is not int for key in original)
            or original["w"] <= 0 or original["h"] <= 0):
        diagnostics["reason"] = "invalid_geometry"
        return original, diagnostics
    if observed_image is None:
        return original, diagnostics
    import numpy as np
    image = observed_image
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim not in (2, 3)
            or (image.ndim == 3 and image.shape[2] not in (3, 4))
            or image.shape[:2] != (original["h"], original["w"])):
        diagnostics["reason"] = "observation_geometry_mismatch"
        return original, diagnostics
    reference_aspect = REFERENCE_WIDTH / REFERENCE_HEIGHT
    aspect = original["w"] / original["h"]
    if abs(aspect - reference_aspect) <= .01:
        diagnostics["reason"] = "reference_aspect"
        return original, diagnostics
    color = image[:, :, :3] if image.ndim == 3 else image
    # Permit slight compression noise while requiring virtually every pixel in
    # a full edge row/column to be black. Alpha is not brightness evidence.
    black = np.max(color, axis=2) <= 6 if color.ndim == 3 else color <= 6
    axis = 0 if aspect > reference_aspect else 1
    edge_lines = np.mean(black, axis=axis) >= .995
    leading = 0
    for value in edge_lines:
        if not value:
            break
        leading += 1
    trailing = 0
    for value in edge_lines[::-1]:
        if not value:
            break
        trailing += 1
    dimension = original["w"] if axis == 0 else original["h"]
    minimum_bar = max(4, round(dimension * .005))
    symmetry_tolerance = max(2, round(dimension * .003))
    diagnostics["observed_bars"] = {"axis": "horizontal" if axis == 0 else "vertical",
                                     "leading": leading, "trailing": trailing}
    if leading < minimum_bar or trailing < minimum_bar:
        diagnostics["reason"] = "no_symmetric_black_bars"
        return original, diagnostics
    if abs(leading - trailing) > symmetry_tolerance:
        diagnostics["reason"] = "asymmetric_bars"
        return original, diagnostics
    content = dict(original)
    if axis == 0:
        content["x"] += leading
        content["w"] -= leading + trailing
        inner = color[:, leading:original["w"]-trailing]
    else:
        content["y"] += leading
        content["h"] -= leading + trailing
        inner = color[leading:original["h"]-trailing, :]
    if (content["w"] <= 0 or content["h"] <= 0
            or content["w"] * content["h"] < original["w"] * original["h"] * .25
            or abs(content["w"] / content["h"] - reference_aspect) > .01):
        diagnostics["reason"] = "content_aspect_mismatch"
        return original, diagnostics
    if not inner.size or int(np.ptp(inner)) < 20 or float(np.mean(inner <= 6)) >= .95:
        diagnostics["reason"] = "no_content_evidence"
        return original, diagnostics
    diagnostics.update(applied=True, reason="observed_black_letterbox",
                       content_box_relative=[content["x"]-original["x"], content["y"]-original["y"],
                                             content["w"], content["h"]])
    return content, diagnostics
