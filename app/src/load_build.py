"""Load validated presets while preserving legacy English template keys."""
import json
import logging
from pathlib import Path

from src.character_catalog import alias_map, normalize_key

logger = logging.getLogger("BuildLoader")


class BuildLoader:
    def __init__(self, build_file_path="builds.json"):
        self.build_data = {}
        self.target_map = {}
        self.aliases = alias_map()
        if build_file_path and not self.load_build(build_file_path):
            raise ValueError(f"빌드 파일을 읽을 수 없습니다: {build_file_path}")

    def load_build(self, path):
        try:
            with Path(path).open(encoding="utf-8-sig") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or not isinstance(data.get("characters"), dict):
                raise ValueError("'characters' 객체가 필요합니다.")
            targets = {}
            for name, skills in data["characters"].items():
                key = self.aliases.get(normalize_key(name), normalize_key(name))
                if key in targets or not isinstance(skills, dict):
                    raise ValueError(f"캐릭터 설정이 올바르지 않습니다: {name}")
                checked = {}
                for potential_id, priority in skills.items():
                    if not str(potential_id).isdigit() or type(priority) is not int or not 0 <= priority <= 5:
                        raise ValueError(f"잠재력 우선순위가 올바르지 않습니다: {name}/{potential_id}")
                    checked[str(potential_id)] = priority
                targets[key] = checked
            self.build_data = data
            self.target_map = targets
            return True
        except (OSError, ValueError, TypeError) as error:
            logger.error("빌드 로드 실패: %s", error)
            self.build_data = {}
            self.target_map = {}
            return False

    def get_priority(self, char_name, filename):
        key = self.aliases.get(normalize_key(char_name), normalize_key(char_name))
        return self.target_map.get(key, {}).get(Path(filename).stem, 0)
