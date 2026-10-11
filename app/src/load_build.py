"""Load legacy priorities and exact targets from verified in-game presets."""
import json
import logging
from pathlib import Path
from typing import Optional

from src.character_catalog import alias_map, load_catalog, normalize_key

logger = logging.getLogger("BuildLoader")


class BuildLoader:
    def __init__(self, build_file_path="builds.json"):
        self.build_data = {}
        self.target_map = {}
        self.target_level_map = {}
        self.is_ingame_preset = False
        self.aliases = alias_map()
        if build_file_path and not self.load_build(build_file_path):
            raise ValueError(f"빌드 파일을 읽을 수 없습니다: {build_file_path}")

    def _character_key(self, name):
        normalized = normalize_key(name)
        return self.aliases.get(normalized, normalized)

    def _load_native_levels(self, data, targets):
        if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
            raise ValueError("게임 프리셋에는 schema_version 2가 필요합니다.")
        raw_levels = data.get("target_levels")
        slots = data.get("slots")
        if not isinstance(raw_levels, dict) or not isinstance(slots, list) or len(slots) != 3:
            raise ValueError("게임 프리셋에는 목표 레벨과 세 캐릭터 슬롯이 필요합니다.")
        levels = {}
        for name, values in raw_levels.items():
            key = self._character_key(name)
            if key in levels or not isinstance(values, dict):
                raise ValueError(f"목표 레벨 캐릭터 설정이 올바르지 않습니다: {name}")
            checked = {}
            for potential_id, level in values.items():
                if not str(potential_id).isdigit() or type(level) is not int or not 0 <= level <= 6:
                    raise ValueError(f"목표 레벨이 올바르지 않습니다: {name}/{potential_id}")
                checked[str(potential_id)] = level
            levels[key] = checked

        catalog = load_catalog()
        positions = set()
        characters = set()
        for slot in slots:
            if not isinstance(slot, dict) or slot.get("position") not in ("master", "assist1", "assist2"):
                raise ValueError("게임 프리셋 슬롯 위치가 올바르지 않습니다.")
            position = slot["position"]
            character = catalog["characters"].get(str(slot.get("character_id")))
            if character is None:
                raise ValueError("게임 프리셋 캐릭터 ID를 찾을 수 없습니다.")
            key = character["key"]
            if position in positions or key in characters or self._character_key(slot.get("key")) != key:
                raise ValueError("게임 프리셋 슬롯이나 캐릭터가 중복되거나 ID와 이름이 다릅니다.")
            positions.add(position)
            characters.add(key)
            schema = character.get("preset_schema", {})
            role = "Master" if position == "master" else "Assist"
            specific = schema.get(f"{role}SpecificPotentialIds")
            normal = schema.get(f"{role}NormalPotentialIds")
            common = schema.get("CommonPotentialIds")
            if (not isinstance(specific, list) or len(specific) != 4
                    or not isinstance(normal, list) or len(normal) != 9
                    or not isinstance(common, list) or len(common) != 3):
                raise ValueError(f"게임 프리셋 잠재력 순서 자료가 없습니다: {key}")
            specific_ids = {str(value) for value in specific}
            expected_ids = {str(value) for value in specific + normal + common}
            if (len(expected_ids) != 16 or key not in levels or key not in targets
                    or set(levels[key]) != expected_ids or set(targets[key]) != expected_ids):
                raise ValueError(f"게임 프리셋의 슬롯별 16개 잠재력이 일치하지 않습니다: {key}")
            if any(levels[key][pid] > 1 for pid in specific_ids):
                raise ValueError(f"고유 잠재력 목표는 0 또는 1이어야 합니다: {key}")
            if any(targets[key][pid] != (2 if level > 0 else 0) for pid, level in levels[key].items()):
                raise ValueError(f"게임 프리셋의 목표와 호환용 우선순위가 일치하지 않습니다: {key}")
        if positions != {"master", "assist1", "assist2"} or set(targets) != characters or set(levels) != characters:
            raise ValueError("게임 프리셋의 캐릭터와 슬롯 목록이 일치하지 않습니다.")
        return levels

    def load_build(self, path):
        try:
            with Path(path).open(encoding="utf-8-sig") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or not isinstance(data.get("characters"), dict):
                raise ValueError("'characters' 객체가 필요합니다.")
            targets = {}
            for name, skills in data["characters"].items():
                key = self._character_key(name)
                if key in targets or not isinstance(skills, dict):
                    raise ValueError(f"캐릭터 설정이 올바르지 않습니다: {name}")
                checked = {}
                for potential_id, priority in skills.items():
                    if not str(potential_id).isdigit() or type(priority) is not int or not 0 <= priority <= 5:
                        raise ValueError(f"잠재력 우선순위가 올바르지 않습니다: {name}/{potential_id}")
                    checked[str(potential_id)] = priority
                targets[key] = checked
            source = data.get("source")
            native = isinstance(source, dict) and source.get("type") == "ingame_preset"
            levels = self._load_native_levels(data, targets) if native else {}
            self.build_data = data
            self.target_map = targets
            self.target_level_map = levels
            self.is_ingame_preset = native
            return True
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.error("빌드 로드 실패: %s", error)
            self.build_data = {}
            self.target_map = {}
            self.target_level_map = {}
            self.is_ingame_preset = False
            return False

    def get_priority(self, char_name, filename):
        return self.target_map.get(self._character_key(char_name), {}).get(Path(filename).stem, 0)

    def get_target_level(self, char_name, filename) -> Optional[int]:
        # SSToy target_levels have a different meaning; legacy overlay behavior remains intact.
        return self.target_level_map.get(self._character_key(char_name), {}).get(Path(filename).stem)

    def get_recognition_characters(self):
        """Only a complete, validated native preset defines an exhaustive party."""
        return frozenset(self.target_level_map) if self.is_ingame_preset else None

    def get_recognition_potentials(self, char_name):
        """Keep all role candidates, including target zero; sparse builds do not filter."""
        if not self.is_ingame_preset:
            return None
        return frozenset(self.target_level_map.get(self._character_key(char_name), {}))
