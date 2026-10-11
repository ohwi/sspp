"""Stable game IDs keep build names independent of localized template filenames."""
import json
import re
from pathlib import Path

from config import RESOURCES_DIR, TEMPLATES_DIR

CATALOG_PATH = RESOURCES_DIR / "characters.json"


def normalize_key(value):
    return re.sub(r"\s+", "_", str(value).strip().casefold())


def load_catalog(path=CATALOG_PATH):
    with Path(path).open(encoding="utf-8-sig") as stream:
        catalog = json.load(stream)
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("characters"), dict):
        raise ValueError("지원하지 않는 캐릭터 목록 형식입니다.")
    return catalog


def alias_map(catalog=None):
    catalog = catalog if catalog is not None else load_catalog()
    aliases = {}
    for char_id, character in catalog["characters"].items():
        key = character["key"]
        for name in [char_id, key, character["name_en"], character["name_ko"], *character.get("aliases", [])]:
            alias = normalize_key(name)
            if alias in aliases and aliases[alias] != key:
                raise ValueError(f"캐릭터 이름이 중복됩니다: {name}")
            aliases[alias] = key
    return aliases


def template_coverage(characters, template_dir=TEMPLATES_DIR):
    """Return missing images for selected build potentials, without claiming recognition."""
    root = Path(template_dir)
    faces = {normalize_key(path.stem) for path in (root / "icons").glob("*") if path.suffix.lower() in (".png", ".jpg", ".jpeg")}
    result = {}
    for key, potentials in characters.items():
        folder = root / "potentials" / key
        present = {path.stem for path in folder.glob("*") if path.suffix.lower() in (".png", ".jpg", ".jpeg")}
        result[key] = {
            "face_available": key in faces,
            "selected_count": len(potentials),
            "missing_potential_ids": sorted(str(value) for value in potentials if str(value) not in present),
        }
    return result
