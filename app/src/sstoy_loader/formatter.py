"""Convert supported SSToy share links to complete ID-validated presets."""
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Any

from src.character_catalog import load_catalog, template_coverage
from src.ingame_preset import decode_preset
from src.target_policy import target_description
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


def _save_preset(result, save_dir, catalog, coverage_characters):
    """Save complete presets without replacing an existing user's file."""
    coverage = template_coverage(coverage_characters)
    warnings = []
    for character in catalog["characters"].values():
        key = character["key"]
        if key not in coverage:
            continue
        status = coverage[key]
        if not status["face_available"] or status["missing_potential_ids"]:
            missing = len(status["missing_potential_ids"])
            warnings.append(f"{character['name_ko']}: 얼굴 이미지 {'있음' if status['face_available'] else '없음'}, 선택한 잠재력 이미지 {status['selected_count'] - missing}/{status['selected_count']}개")
    result["template_coverage"] = coverage
    result["warnings"] = warnings
    directory = Path(save_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{sanitize_filename(result['build_name'])}.json"
    number = 2
    while target.exists():
        target = directory / f"{sanitize_filename(result['build_name'])} ({number}).json"
        number += 1
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return True, str(target)


def save_game_preset_to_json(code: str, save_dir: str, build_name: str = ""):
    """Import the game's exact goals; the share code has no priority marks."""
    try:
        catalog = load_catalog()
        decoded = decode_preset(code, catalog)
        characters, levels, slots, selected = {}, {}, [], {}
        names = []
        for slot in decoded["slots"]:
            char_id = str(slot["char_id"])
            if char_id == "0":
                continue
            character = catalog["characters"][char_id]
            key = character["key"]
            if key in characters:
                raise ValueError("같은 캐릭터가 두 슬롯에 있습니다.")
            exact = {str(pid): level for pid, level in slot["target_levels"].items()}
            # Retain the legacy integer map for callers, but native overlays use
            # target_levels directly and never label these as priority marks.
            characters[key] = {pid: 2 if level > 0 else 0 for pid, level in exact.items()}
            levels[key] = exact
            selected[key] = {pid: 2 for pid, level in exact.items() if level > 0}
            slots.append({"position": slot["position"], "character_id": int(char_id), "key": key})
            names.append(character["name_ko"])
        if not characters:
            raise ValueError("캐릭터가 없는 게임 프리셋입니다.")
        result = {
            "schema_version": 2,
            "build_name": build_name.strip() or " · ".join(names),
            "characters": characters,
            "target_levels": levels,
            "slots": slots,
            "source": {"type": "ingame_preset", "code": "".join(code.split()),
                       "catalog_revision": catalog["source"]["revision"]},
        }
        return _save_preset(result, save_dir, catalog, selected)
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.error("게임 프리셋 가져오기 실패: %s", error)
        return False, f"게임 프리셋 가져오기 실패: {error}"


def describe_game_preset(result, catalog=None):
    """Describe goal policy while retaining intermediate preset values."""
    catalog = catalog if catalog is not None else load_catalog()
    roles = {"master": "메인", "assist1": "서포트 1", "assist2": "서포트 2"}
    lines = []
    for slot in result["slots"]:
        character = catalog["characters"][str(slot["character_id"])]
        lines.append(f"{roles[slot['position']]}: {character['name_ko']}")
        names = character.get("potential_names", {})
        for pid, level in result["target_levels"][slot["key"]].items():
            if level > 0:
                lines.append(f"  {names.get(pid, pid)}: {target_description(level)}")
    return "\n".join(lines)


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
        result = {
            "build_name": decoded.get("build_name") or "새로운 빌드",
            "characters": characters,
            "target_levels": levels,
            "source": {"type": "sstoy", "share_version": decoded["version"],
                       "catalog_revision": catalog["source"]["revision"]},
        }
        return _save_preset(result, save_dir, catalog, characters)
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.error("빌드 변환 실패: %s", error)
        return False, f"빌드 변환 실패: {error}"


def run(url: str, output_path: str):
    return save_build_to_json(url, os.path.dirname(output_path) or ".")
