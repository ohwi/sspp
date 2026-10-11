"""Refresh the ID/name catalog from an explicitly pinned SSToy commit.

This updates metadata only; recognition images require game-screen validation.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVISION = "34f57606015277cf9da76a2c7cc14fa1b68ccbbf"
DEFAULT_CONFIG_REVISION = "225c0b76027f7345a712a2d62dbe7ee22de2f836"
PATHS = {"characters": "Character.json", "potentials": "Potential.json", "pools": "CharPotential.json",
         "en": "EN/Character.json", "ko": "KR/Character.json",
         "items": "Item.json", "item_names": "KR/Item.json"}
PRESET_FIELDS = {"MasterSpecificPotentialIds": 4, "MasterNormalPotentialIds": 9,
                 "AssistSpecificPotentialIds": 4, "AssistNormalPotentialIds": 9,
                 "CommonPotentialIds": 3}


def collect(revision, data_dir=None, config_revision=DEFAULT_CONFIG_REVISION):
    data = {}
    hashes = {}
    for key, path in PATHS.items():
        if data_dir:
            local = Path(data_dir) / path.replace("/", "_")
            raw = local.read_bytes()
        else:
            url = f"https://raw.githubusercontent.com/JforPlay/sstoy/{revision}/public/data/{path}"
            with urlopen(url, timeout=20) as response:
                raw = response.read()
        hashes[path] = hashlib.sha256(raw).hexdigest()
        data[key] = json.loads(raw.decode("utf-8-sig"))
    config_path = "KR/bin/Config.json"
    config_url = f"https://raw.githubusercontent.com/Hiro420/StellaSoraData/{config_revision}/{config_path}"
    if data_dir:
        raw = (Path(data_dir) / "KR_Config.json").read_bytes()
    else:
        with urlopen(config_url, timeout=20) as response:
            raw = response.read()
    data["ingame_config"] = json.loads(raw.decode("utf-8-sig"))
    data["ingame_config_source"] = {"repository": "Hiro420/StellaSoraData", "revision": config_revision,
                                    "path": config_path, "url": config_url,
                                    "sha256": hashlib.sha256(raw).hexdigest()}
    return data, hashes


def make_catalog(data, hashes, revision):
    max_level = int(data["ingame_config"]["PotentialPreselectionMaxLevel"]["Value"])
    if not 1 <= max_level <= 7:
        raise ValueError("게임 프리셋 최대 목표 레벨이 3비트 범위를 벗어납니다.")
    previous_path = ROOT / "app/resources/characters.json"
    previous = json.loads(previous_path.read_text("utf-8-sig"))["characters"] if previous_path.exists() else {}
    existing = {}
    for folder in (ROOT / "app/resources/templates/potentials").iterdir():
        if not folder.is_dir():
            continue
        for image in folder.glob("*.png"):
            potential = data["potentials"].get(image.stem)
            if potential:
                existing[str(potential["CharId"])] = folder.name.casefold()
                break
    result = {}
    for char_id, character in sorted(data["characters"].items(), key=lambda item: int(item[0])):
        if not character.get("Visible") or not character.get("Available"):
            continue
        name_key = character["Name"]
        en = data["en"][name_key]
        ko = data["ko"][name_key]
        key = previous.get(char_id, {}).get("key") or existing.get(char_id) or re.sub(r"\s+", "_", en.strip().casefold())
        pool = data["pools"].get(char_id)
        if not pool:
            raise ValueError(f"캐릭터 {char_id}의 선택 가능한 잠재력 목록이 없습니다.")
        preset_schema = {}
        for field, count in PRESET_FIELDS.items():
            values = pool.get(field)
            if (not isinstance(values, list) or len(values) != count
                    or any(type(value) is not int or value <= 0 for value in values)
                    or len(set(values)) != count):
                raise ValueError(f"캐릭터 {char_id}의 {field} 배열이 예상 형식({count}개)과 다릅니다.")
            preset_schema[field] = list(values)
        potential_ids = {str(value) for values in preset_schema.values() for value in values}
        if len(potential_ids) != 29:
            raise ValueError(f"캐릭터 {char_id}의 프리셋 잠재력 합집합이 29개가 아닙니다.")
        potentials = {}
        potential_names = {}
        for pid in sorted(potential_ids, key=int):
            value = data["potentials"][pid]
            if str(value["CharId"]) != char_id:
                raise ValueError(f"잠재력 {pid}의 캐릭터가 선택 목록과 다릅니다.")
            potentials[pid] = int(value["MaxLevel"])
            name = data["item_names"].get(data["items"][pid]["Title"])
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"잠재력 {pid}의 한국어 이름이 없습니다.")
            potential_names[pid] = name
        if not potentials:
            raise ValueError(f"캐릭터 {char_id}의 잠재력 자료가 없습니다.")
        result[char_id] = {"key": key, "name_en": en, "name_ko": ko, "aliases": [],
                           "potentials": dict(sorted(potentials.items(), key=lambda item: int(item[0]))),
                           "preset_schema": preset_schema, "potential_names": potential_names}
    return {"schema_version": 1,
            "source": {"repository": "JforPlay/sstoy", "revision": revision, "sha256": hashes,
                       "ingame_preset_config": data["ingame_config_source"]},
            "ingame_preset_max_level": max_level,
            "characters": result}


def audit(catalog):
    faces = {path.stem.casefold() for path in (ROOT / "app/resources/templates/icons").glob("*.png")}
    rows = []
    for char_id, character in catalog["characters"].items():
        present = {path.stem for path in (ROOT / "app/resources/templates/potentials" / character["key"]).glob("*.png")}
        rows.append({"id": int(char_id), "key": character["key"], "name_ko": character["name_ko"],
                     "face_available": character["key"] in faces,
                     "expected": len(character["potentials"]),
                     "missing_ids": sorted(set(character["potentials"]) - present, key=int),
                     "unmapped_files": sorted(present - set(character["potentials"]))})
    return {"source_revision": catalog["source"]["revision"], "characters": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--config-revision", default=DEFAULT_CONFIG_REVISION)
    parser.add_argument("--data-dir", type=Path,
                        help="Offline: Character.json, Potential.json, CharPotential.json, EN_Character.json, "
                             "KR_Character.json, Item.json, KR_Item.json, KR_Config.json")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("--revision에는 고정된 40자리 commit SHA가 필요합니다.")
    if not re.fullmatch(r"[0-9a-f]{40}", args.config_revision):
        parser.error("--config-revision에는 고정된 40자리 commit SHA가 필요합니다.")
    if args.audit_only:
        catalog = json.loads((ROOT / "app/resources/characters.json").read_text("utf-8-sig"))
    else:
        data, hashes = collect(args.revision, args.data_dir, args.config_revision)
        catalog = make_catalog(data, hashes, args.revision)
        target = ROOT / "app/resources/characters.json"
        target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit(catalog), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
