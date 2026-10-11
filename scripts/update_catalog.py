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
PATHS = {"characters": "Character.json", "potentials": "Potential.json", "pools": "CharPotential.json",
         "en": "EN/Character.json", "ko": "KR/Character.json"}


def collect(revision, data_dir=None):
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
    return data, hashes


def make_catalog(data, hashes, revision):
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
        potential_ids = {str(value) for field, values in pool.items() if field.endswith("PotentialIds") for value in values}
        potentials = {}
        for pid in sorted(potential_ids, key=int):
            value = data["potentials"][pid]
            if str(value["CharId"]) != char_id:
                raise ValueError(f"잠재력 {pid}의 캐릭터가 선택 목록과 다릅니다.")
            potentials[pid] = int(value["MaxLevel"])
        if not potentials:
            raise ValueError(f"캐릭터 {char_id}의 잠재력 자료가 없습니다.")
        result[char_id] = {"key": key, "name_en": en, "name_ko": ko, "aliases": [],
                           "potentials": dict(sorted(potentials.items(), key=lambda item: int(item[0])))}
    return {"schema_version": 1, "source": {"repository": "JforPlay/sstoy", "revision": revision, "sha256": hashes},
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
    parser.add_argument("--data-dir", type=Path, help="Offline files named Character.json, Potential.json, EN_Character.json, KR_Character.json")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("--revision에는 고정된 40자리 commit SHA가 필요합니다.")
    if args.audit_only:
        catalog = json.loads((ROOT / "app/resources/characters.json").read_text("utf-8-sig"))
    else:
        data, hashes = collect(args.revision, args.data_dir)
        catalog = make_catalog(data, hashes, args.revision)
        target = ROOT / "app/resources/characters.json"
        target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit(catalog), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
