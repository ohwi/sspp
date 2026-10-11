"""Convert supported SSToy share links to complete ID-validated presets."""
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Any

from src.character_catalog import load_catalog, template_coverage
from . import decoder

logger = logging.getLogger("Formatter")


def sanitize_filename(name: str) -> str:
    safe = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", name).strip().rstrip(". ")
    # Windows reserves these names even with an extension.
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", safe):
        safe = "_" + safe
    return safe or "Untitled_Build"


def build_id_mapping(db_json: Dict[str, Any]) -> Dict[int, str]:
    """Legacy helper: take only the actual character name, never other text rows."""
    result = {}
    for key, value in db_json.items():
        match = re.fullmatch(r"Character\.(\d+)\.1", key)
        if isinstance(value, str) and (match or key.isdigit()):
            result[int(match.group(1) if match else key)] = value
    return result


def save_build_to_json(url: str, save_dir: str):
    decoded = decoder.decode_url_raw(url)
    if "error" in decoded:
        return False, f"디코딩 오류: {decoded['error']}"
    try:
        catalog = load_catalog()
        characters = {}
        levels = {}
        for position in ("master", "assist1", "assist2"):
            data = decoded.get("raw_characters", {}).get(position)
            if data is None:
                continue
            char_id = str(data["mapped_char_idx"])
            character = catalog["characters"].get(char_id)
            if character is None:
                raise ValueError(f"캐릭터 ID {char_id}의 자료가 없습니다. 캐릭터 목록을 업데이트하세요.")
            key = character["key"]
            if key in characters:
                raise ValueError(f"같은 캐릭터가 두 슬롯에 있습니다: {character['name_ko']}")
            priorities = {}
            for potential_id in data["mapped_potentials"]:
                pid = str(potential_id)
                if pid not in character["potentials"]:
                    raise ValueError(f"{character['name_ko']}의 잠재력 ID {pid}가 목록과 일치하지 않습니다.")
                priority = data["marks"].get(potential_id, 2)
                # Single-level potentials cannot be upgraded to level six.
                if character["potentials"][pid] == 1:
                    priority = {5: 3, 4: 2}.get(priority, priority)
                priorities[pid] = priority
            characters[key] = priorities
            levels[key] = {str(pid): level for pid, level in data.get("potential_levels", {}).items()}
        if not characters:
            raise ValueError("캐릭터가 없는 빌드입니다.")
        coverage = template_coverage(characters)
        warnings = []
        for char_id, character in catalog["characters"].items():
            key = character["key"]
            if key not in coverage:
                continue
            status = coverage[key]
            if not status["face_available"] or status["missing_potential_ids"]:
                missing = len(status["missing_potential_ids"])
                warnings.append(f"{character['name_ko']}: 얼굴 이미지 {'있음' if status['face_available'] else '없음'}, 선택한 잠재력 이미지 {status['selected_count'] - missing}/{status['selected_count']}개")
        result = {
            "build_name": decoded.get("build_name") or "새로운 빌드",
            "characters": characters,
            "target_levels": levels,
            "source": {"type": "sstoy", "share_version": decoded["version"],
                       "catalog_revision": catalog["source"]["revision"]},
            "template_coverage": coverage,
            "warnings": warnings,
        }
        directory = Path(save_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{sanitize_filename(result['build_name'])}.json"
        # Preserve an existing preset; an import never silently replaces it.
        number = 2
        while target.exists():
            target = directory / f"{sanitize_filename(result['build_name'])} ({number}).json"
            number += 1
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return True, str(target)
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.error("빌드 변환 실패: %s", error)
        return False, f"빌드 변환 실패: {error}"


def run(url: str, output_path: str):
    return save_build_to_json(url, os.path.dirname(output_path) or ".")
