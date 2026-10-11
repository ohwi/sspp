"""Audit scratch image candidates without registering or changing production assets.

Run --freeze before generation, then run without --freeze after both manifests
exist. Exit 0 means file/provenance checks passed; it does not validate matching
in the game. Exit 1 means a failed check, and exit 2 means generation is pending.
Uses the app's existing numpy/opencv dependencies and never performs network I/O.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAP_FILES = {
    "Character.json": "Character.json",
    "Potential.json": "Potential.json",
    "CharPotential.json": "CharPotential.json",
    "Item.json": "Item.json",
    "KR/Item.json": "KR_Item.json",
    "KR/Character.json": "KR_Character.json",
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def image_info(path, png_required=False):
    raw = Path(path).read_bytes()
    if png_required and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None or decoded.size == 0:
        raise ValueError("image cannot be decoded")
    if decoded.dtype != np.uint8 or decoded.ndim not in (2, 3):
        raise ValueError("expected an 8-bit image")
    # Pixel hashes include shape/channel information, independent of PNG encoding.
    prefix = json.dumps([list(decoded.shape), str(decoded.dtype)]).encode("ascii") + b"\0"
    channels = decoded.shape[2] if decoded.ndim == 3 else 1
    visible = decoded[..., :3] if channels in (3, 4) else decoded
    nonempty = bool(np.ptp(visible) > 0)
    if channels == 4:
        nonempty = nonempty and bool(np.any(decoded[..., 3] > 0))
    return {
        "sha256": sha256(raw), "bytes": len(raw),
        "width": int(decoded.shape[1]), "height": int(decoded.shape[0]),
        "channels": int(channels), "decoded_sha256": sha256(prefix + decoded.tobytes()),
        "nonempty": nonempty,
    }


def production_snapshot(repo):
    templates = repo / "app/resources/templates"
    images = sorted(p for p in templates.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    records = {}
    for path in images:
        records[path.relative_to(repo).as_posix()] = image_info(path)
    normalized_paths = {path.casefold() for path in records}
    catalog_path = repo / "app/resources/characters.json"
    catalog = read_json(catalog_path)
    expected = {}
    missing_faces = {}
    for char_id, char in catalog["characters"].items():
        key = char["key"]
        face = f"app/resources/templates/icons/{key}.png"
        if face.casefold() not in normalized_paths:
            missing_faces[key] = {"character_id": int(char_id), "character_key": key, "name_ko": char["name_ko"]}
        for pid in char["potentials"]:
            path = f"app/resources/templates/potentials/{key}/{pid}.png"
            if path.casefold() not in normalized_paths:
                expected[str(pid)] = {"character_id": int(char_id), "character_key": key,
                                      "name_ko": char["potential_names"][str(pid)]}
    return {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(repo), "catalog_sha256": sha256(catalog_path.read_bytes()),
        "source_revision": catalog["source"]["revision"], "images": records,
        "counts": {"all_images": len(records),
                   "potentials": sum("/potentials/" in p for p in records),
                   "faces": sum("/icons/" in p for p in records)},
        "expected_missing_potentials": expected, "expected_missing_faces": missing_faces,
    }


class Audit:
    def __init__(self):
        self.checks = []
        self.flags = []
        self.source_trees = {}
        self.manifest_receipts = {}

    def check(self, name, passed, detail=None, pending=False):
        row = {"check": name, "status": "pending" if pending else "pass" if passed else "fail"}
        if detail is not None:
            row["detail"] = detail
        self.checks.append(row)
        return passed

    def flag(self, name, detail):
        self.flags.append({"flag": name, "status": "not_game_verified", "detail": detail})


def checked_maps(catalog, data_dir, audit):
    tables = {}
    for source_path, local_name in MAP_FILES.items():
        path = data_dir / local_name
        if not audit.check("mapping_file_exists:" + source_path, path.is_file(), str(path)):
            continue
        expected = catalog["source"]["sha256"].get(source_path)
        audit.check("mapping_pinned_hash:" + source_path, expected == sha256(path.read_bytes()),
                    {"expected": expected, "actual": sha256(path.read_bytes())})
        tables[source_path] = read_json(path)
    if len(tables) != len(MAP_FILES):
        return None
    for char_id, char in catalog["characters"].items():
        original = tables["Character.json"].get(char_id, {})
        audit.check("catalog_character_mapping:" + char_id,
                    original.get("Id") == int(char_id)
                    and tables["KR/Character.json"].get(original.get("Name")) == char["name_ko"])
        pool = tables["CharPotential.json"].get(char_id, {})
        audit.check("catalog_preset_schema:" + char_id,
                    all(pool.get(field) == values for field, values in char["preset_schema"].items()))
        for pid in char["potentials"]:
            potential = tables["Potential.json"].get(pid, {})
            item = tables["Item.json"].get(pid, {})
            audit.check("catalog_potential_mapping:" + pid,
                        potential.get("Id") == int(pid) and potential.get("CharId") == int(char_id)
                        and item.get("Id") == int(pid)
                        and tables["KR/Item.json"].get(item.get("Title")) == char["potential_names"][pid])
    return tables


def path_under(root, value):
    path = (root / str(value)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("candidate path leaves the scratch output directory")
    return path


def source_entries(manifest):
    sources = manifest.get("sources", {})
    if isinstance(sources, list):
        return {str(row.get("id", i)): row for i, row in enumerate(sources)}
    return sources if isinstance(sources, dict) else {}


def audit_sources(manifest, output_dir, audit, label):
    sources = source_entries(manifest)
    audit.check(label + ":source_receipts_present", bool(sources))
    valid = set()
    for ref, source in sources.items():
        prefix = label + ":source:" + str(ref)
        if not isinstance(source, dict):
            audit.check(prefix, False, "source receipt is not an object")
            continue
        value = source.get("local_path")
        path = Path(value) if value else None
        if path and not path.is_absolute():
            path = output_dir / path
        exists = bool(path and path.is_file())
        audit.check(prefix + ":exists", exists, str(path))
        if not exists:
            continue
        raw = path.read_bytes()
        hash_ok = source.get("sha256") == sha256(raw)
        audit.check(prefix + ":sha256", hash_ok)
        if source.get("bytes") is not None:
            audit.check(prefix + ":byte_count", source["bytes"] == len(raw))
        if source.get("width") is not None or source.get("height") is not None:
            try:
                decoded = image_info(path)
                audit.check(prefix + ":decoded_dimensions", decoded["width"] == source.get("width")
                            and decoded["height"] == source.get("height"))
            except (OSError, ValueError) as exc:
                audit.check(prefix + ":decode", False, str(exc))
        blob = source.get("git_blob_sha")
        if blob:
            git_hash = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
            blob_ok = re.fullmatch(r"[0-9a-f]{40}", str(blob)) is not None and blob == git_hash
            audit.check(prefix + ":git_blob", blob_ok)
        else:
            blob_ok = False
            audit.check(prefix + ":git_blob", False, "missing pinned Git blob receipt")
        pinned = bool(re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision", ""))))
        audit.check(prefix + ":pinned_source", pinned and bool(source.get("repository"))
                    and bool(source.get("path")) and bool(source.get("url")))
        repository, revision, source_path = source.get("repository"), source.get("revision"), source.get("path")
        expected_url = f"https://raw.githubusercontent.com/{repository}/{revision}/{source_path}"
        audit.check(prefix + ":url_mapping", source.get("url") == expected_url)
        tree = audit.source_trees.get(repository)
        membership = bool(tree and tree["revision"] == revision and tree["files"].get(source_path) == blob)
        audit.check(prefix + ":pinned_tree_membership", membership,
                    {"repository": repository, "revision": revision, "path": source_path})
        if hash_ok and blob_ok and pinned and membership:
            valid.add(str(ref))
    return sources, valid


def normalize_faces(manifest):
    """Adapt the face generator's separate primary/variant receipt contract."""
    if "candidates" not in manifest:
        return manifest
    result = dict(manifest)
    result["files"], result["variant_files"], result["sources"] = [], [], {}
    for index, candidate in enumerate(manifest["candidates"]):
        row = dict(candidate)
        row["character_key"] = row.get("key")
        row["relative_path"] = row.get("relative_output_file")
        source = dict(row.get("source", {}))
        source["revision"] = source.get("commit")
        source["local_path"] = source.get("cache_file")
        ref = f"face-source-{index}"
        result["sources"][ref] = source
        row["source_refs"] = [ref]
        result["files" if row.get("primary") else "variant_files"].append(row)
    result["missing_count"] = manifest.get("primary_count")
    is_unregistered = (manifest.get("live_game_validation_performed") is False
                       and all(row.get("status") == "candidate_unvalidated" for row in manifest["candidates"]))
    result["status"] = "candidate_not_registered" if is_unregistered else manifest.get("status")
    return result


def load_source_trees(data_dir, audit):
    for repository, name in (("JforPlay/sstoy", "sstoy"), ("AutumnVN/ssassets", "ssassets")):
        path = data_dir / "db-coverage" / (name + "_tree.json")
        if not audit.check("source_tree_exists:" + repository, path.is_file(), str(path)):
            continue
        tree = read_json(path)
        valid = tree.get("truncated") is False and isinstance(tree.get("tree"), list)
        audit.check("source_tree_complete:" + repository, valid)
        if valid:
            audit.source_trees[repository] = {"revision": tree.get("sha"), "cache_path": str(path),
                                            "cache_sha256": sha256(path.read_bytes()),
                                            "files": {row["path"]: row["sha"] for row in tree["tree"] if row.get("type") == "blob"}}


def audit_face_identity(row, source, tables, audit, prefix):
    char = tables["Character.json"].get(str(row.get("character_id")), {}) if tables else {}
    variant = row.get("variant")
    advance = variant == "advance_L"
    skin_id = char.get("AdvanceSkinId" if advance else "DefaultSkinId")
    suffix = "SS" if variant == "default_SS" else "L"
    extension = ".png" if variant == "primary_default_L" else ".webp"
    expected_basename = f"head_{skin_id}_{suffix}{extension}"
    audit.check(prefix + ":raw_skin_variant",
                variant in {"primary_default_L", "advance_L", "default_SS"}
                and row.get("skin_id") == skin_id and row.get("skin_kind") == ("advance" if advance else "default")
                and Path(source.get("path", "")).name == expected_basename)


def audit_manifest(label, manifest_path, expected, output_dir, tables, audit):
    if not manifest_path.is_file():
        audit.check(label + ":manifest_exists", False, str(manifest_path), pending=True)
        return [], None
    manifest = read_json(manifest_path)
    audit.manifest_receipts[label] = {"path": str(manifest_path), "sha256": sha256(manifest_path.read_bytes())}
    if manifest.get("script") or manifest.get("script_sha256"):
        generator = Path(str(manifest.get("script", "")))
        generator_ok = generator.is_file() and sha256(generator.read_bytes()) == manifest.get("script_sha256")
        audit.check(label + ":generator_script_hash", generator_ok)
        audit.manifest_receipts[label]["generator_path"] = str(generator)
        audit.manifest_receipts[label]["generator_sha256"] = manifest.get("script_sha256")
    if label == "faces":
        manifest = normalize_faces(manifest)
    rows = manifest.get("controls" if label == "controls" else "files", [])
    audit.check(label + ":manifest_schema", manifest.get("schema_version") == 1 and isinstance(rows, list))
    audit.check(label + ":candidate_status", manifest.get("status") == "candidate_not_registered")
    sources, valid_sources = audit_sources(manifest, output_dir, audit, label)
    is_potential = label in {"potentials", "controls"}
    identity_field = "potential_id" if is_potential else "character_key"
    identities = [str(row.get(identity_field)) for row in rows]
    audit.check(label + ":unique_ids", len(set(identities)) == len(identities))
    audit.check(label + ":exact_missing_ids", set(identities) == set(expected),
                {"missing": sorted(set(expected) - set(identities)), "unexpected": sorted(set(identities) - set(expected))})
    declared_count = len(rows) if label == "controls" else manifest.get("missing_count", len(rows))
    audit.check(label + ":declared_count", declared_count == len(expected) == len(rows))
    size = 150 if is_potential else 65
    records = []
    declared_paths = set()
    for row in rows:
        identity = str(row.get(identity_field))
        prefix = label + ":" + identity
        mapping = expected.get(identity)
        audit.check(prefix + ":expected_mapping", bool(mapping) and all(row.get(k) == v for k, v in mapping.items()))
        expected_path = (f"{label}/{mapping['character_key']}/{identity}.png" if is_potential
                         else f"icons/{identity}.png") if mapping else None
        relative = row.get("relative_path")
        audit.check(prefix + ":path_mapping", relative == expected_path)
        try:
            path = path_under(output_dir, relative)
        except (ValueError, TypeError) as exc:
            audit.check(prefix + ":safe_path", False, str(exc))
            continue
        declared_paths.add(path)
        if not audit.check(prefix + ":file_exists", path.is_file(), str(path)):
            continue
        try:
            info = image_info(path, png_required=True)
        except (ValueError, OSError) as exc:
            audit.check(prefix + ":decode", False, str(exc))
            continue
        audit.check(prefix + ":size", info["width"] == size == info["height"])
        audit.check(prefix + ":nonempty", info["nonempty"])
        audit.check(prefix + ":color_channels", info["channels"] in (3, 4))
        audit.check(prefix + ":manifest_hash", info["sha256"] == row.get("sha256"))
        audit.check(prefix + ":manifest_dimensions", info["width"] == row.get("width") and info["height"] == row.get("height"))
        if row.get("bytes") is not None:
            audit.check(prefix + ":manifest_byte_count", info["bytes"] == row["bytes"])
        if row.get("channels") is not None:
            audit.check(prefix + ":manifest_channels", info["channels"] == row["channels"])
        if row.get("decoded_sha256") is not None:
            audit.check(prefix + ":manifest_decoded_hash", info["decoded_sha256"] == row["decoded_sha256"])
        audit.check(prefix + ":transform_recorded", bool(row.get("transform")))
        refs = row.get("source_refs", [])
        audit.check(prefix + ":source_refs", isinstance(refs, list) and bool(refs)
                    and all(str(ref) in sources and str(ref) in valid_sources for ref in refs))
        if is_potential and tables and identity in tables["Potential.json"]:
            potential = tables["Potential.json"][identity]
            item = tables["Item.json"].get(identity, {})
            audit.check(prefix + ":raw_rarity_corner_stype",
                        row.get("corner") == potential.get("Corner") and row.get("rarity") == item.get("Rarity")
                        and row.get("stype") == item.get("Stype"))
            info["glyph"] = item.get("Icon")
            ref_paths = {Path(sources[str(ref)].get("path", "")).name for ref in refs if str(ref) in sources}
            glyph = Path(item.get("Icon", "")).name + "_A.png"
            audit.check(prefix + ":glyph_source_mapping", glyph in ref_paths)
            background = 6 if item.get("Stype") == 42 else 4 if item.get("Rarity") == 2 else 5
            audit.check(prefix + ":background_source_mapping", f"rare_vestige_card_s_{background}.png" in ref_paths)
            corner = {1: "Diamond", 2: "Triangle", 3: "Round"}.get(potential.get("Corner"))
            if potential.get("Corner"):
                audit.check(prefix + ":corner_source_mapping", bool(corner) and
                            all(f"Potential_{corner}_{suffix}.png" in ref_paths for suffix in ("A", "B")))
        elif label == "faces" and refs and str(refs[0]) in sources:
            audit_face_identity(row, sources[str(refs[0])], tables, audit, prefix)
        info.update({"id": identity, "character_key": row.get("character_key"), "relative_path": relative,
                     "rarity": row.get("rarity"), "corner": row.get("corner"), "stype": row.get("stype")})
        records.append(info)
    folder = output_dir / (label if is_potential else "icons")
    primary_paths = folder.glob("*/*") if is_potential else folder.glob("*")
    actual = {p.resolve() for p in primary_paths if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES}
    audit.check(label + ":no_undeclared_images", actual == declared_paths,
                {"undeclared": sorted(str(p) for p in actual - declared_paths),
                 "declared_missing": sorted(str(p) for p in declared_paths - actual)})
    return records, manifest


def audit_face_variants(manifest, expected, output_dir, tables, audit):
    if manifest is None:
        return []
    rows = manifest.get("variant_files", [])
    sources = source_entries(manifest)
    desired = {(key, variant) for key in expected for variant in ("advance_L", "default_SS")}
    actual_ids = [(row.get("character_key"), row.get("variant")) for row in rows]
    audit.check("faces:exact_variant_ids", len(actual_ids) == len(set(actual_ids)) and set(actual_ids) == desired)
    audit.check("faces:variant_manifest_counts", manifest.get("variant_count") == len(rows)
                and manifest.get("total_count") == len(rows) + len(manifest.get("files", [])))
    decoded = []
    declared_paths = set()
    for row in rows:
        key, variant = row.get("character_key"), row.get("variant")
        prefix = f"face_variant:{key}:{variant}"
        mapping = expected.get(key)
        audit.check(prefix + ":character_mapping", bool(mapping) and all(row.get(k) == v for k, v in mapping.items()))
        relative = f"icons/variants/{variant}/{key}.png"
        audit.check(prefix + ":path_mapping", row.get("relative_path") == relative)
        try:
            path = path_under(output_dir, row.get("relative_path"))
            declared_paths.add(path)
            info = image_info(path, png_required=True)
        except (ValueError, OSError) as exc:
            audit.check(prefix + ":decode", False, str(exc))
            continue
        audit.check(prefix + ":size_nonempty", info["width"] == 65 == info["height"] and info["nonempty"])
        audit.check(prefix + ":manifest_hash_dimensions", info["sha256"] == row.get("sha256")
                    and info["width"] == row.get("width") and info["height"] == row.get("height")
                    and info["channels"] == row.get("channels"))
        audit.check(prefix + ":transform_recorded", bool(row.get("transform")))
        refs = row.get("source_refs", [])
        if refs and str(refs[0]) in sources:
            audit_face_identity(row, sources[str(refs[0])], tables, audit, prefix)
        else:
            audit.check(prefix + ":source_ref", False)
        info.update({"id": key, "variant": variant, "relative_path": relative})
        decoded.append(info)
    variant_folder = output_dir / "icons/variants"
    actual_paths = {p.resolve() for p in variant_folder.glob("*/*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES}
    audit.check("faces:no_undeclared_variants", actual_paths == declared_paths)
    return decoded


def duplicate_groups(records, field):
    grouped = defaultdict(list)
    for row in records:
        value = row.get(field)
        if value:
            grouped[value].append({k: row.get(k) for k in ("id", "character_key", "relative_path", "glyph", "rarity", "corner", "stype")})
    return [values for values in grouped.values() if len(values) > 1]


def collision_scopes(groups):
    within, across = [], []
    for group in groups:
        by_character = defaultdict(list)
        for row in group:
            by_character[row["character_key"]].append(row)
        for char_key, rows in by_character.items():
            if len({row["id"] for row in rows}) > 1:
                within.append({"character_key": char_key, "files": rows})
        if len(by_character) > 1:
            across.append(group)
    return {"same_character_different_pids": within, "different_characters": across}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT.parent / "generated-templates")
    parser.add_argument("--data-dir", type=Path, default=ROOT.parent / "asset-audit")
    parser.add_argument("--freeze", action="store_true", help="Create an immutable pre-generation production snapshot")
    args = parser.parse_args()
    repo, output = args.repo_root.resolve(), args.output_dir.resolve()
    baseline_path = output / "production-baseline.json"
    current = production_snapshot(repo)
    if args.freeze:
        if baseline_path.exists():
            existing = read_json(baseline_path)
            if existing["images"] != current["images"] or existing["catalog_sha256"] != current["catalog_sha256"]:
                raise ValueError("baseline already exists and production differs; refusing to replace it")
        else:
            write_json(baseline_path, current)
        print(json.dumps({"status": "production_frozen", "baseline": str(baseline_path), "counts": current["counts"],
                          "missing_potentials": len(current["expected_missing_potentials"]),
                          "missing_faces": len(current["expected_missing_faces"])}, ensure_ascii=False))
        return 0
    if not baseline_path.exists():
        raise ValueError("run --freeze before generation; no implicit baseline is allowed")
    baseline = read_json(baseline_path)
    audit = Audit()
    before, after = baseline["images"], current["images"]
    audit.check("production_images_unchanged", before == after,
                {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
                 "changed": sorted(p for p in set(before) & set(after) if before[p] != after[p])})
    audit.check("production_catalog_unchanged", baseline["catalog_sha256"] == current["catalog_sha256"])
    catalog = read_json(repo / "app/resources/characters.json")
    tables = checked_maps(catalog, args.data_dir.resolve(), audit)
    load_source_trees(args.data_dir.resolve(), audit)
    potentials, potential_manifest = audit_manifest("potentials", output / "potential-manifest.json",
                        baseline["expected_missing_potentials"], output, tables, audit)
    controls = []
    if potential_manifest is not None:
        all_potentials = {pid: {"character_id": int(char_id), "character_key": char["key"],
                               "name_ko": char["potential_names"][pid]}
                          for char_id, char in catalog["characters"].items() for pid in char["potentials"]}
        control_ids = {str(row.get("potential_id")) for row in potential_manifest.get("controls", [])}
        expected_controls = {pid: all_potentials[pid] for pid in control_ids if pid in all_potentials}
        controls, _ = audit_manifest("controls", output / "potential-manifest.json",
                                    expected_controls, output, tables, audit)
        audit.check("controls:outside_missing_candidates", not (control_ids & set(baseline["expected_missing_potentials"])))
        calibration = potential_manifest.get("calibration", {})
        try:
            calibration_path = path_under(output, calibration.get("relative_path"))
            audit.check("potentials:calibration_receipt", calibration_path.is_file()
                        and sha256(calibration_path.read_bytes()) == calibration.get("sha256"))
        except (ValueError, OSError) as exc:
            audit.check("potentials:calibration_receipt", False, str(exc))
    faces, face_manifest = audit_manifest("faces", output / "faces-manifest.json",
                        baseline["expected_missing_faces"], output, tables, audit)
    variants = audit_face_variants(face_manifest, baseline["expected_missing_faces"], output, tables, audit)
    production_potentials = [{**info, "id": Path(path).stem, "character_key": Path(path).parent.name,
                              "relative_path": path} for path, info in before.items() if "/potentials/" in path]
    groups = {"candidate_identical_pixels": duplicate_groups(potentials, "decoded_sha256"),
              "production_identical_pixels": duplicate_groups(production_potentials, "decoded_sha256"),
              "combined_identical_pixels": duplicate_groups(production_potentials + potentials, "decoded_sha256"),
              "candidate_shared_glyph": duplicate_groups(potentials, "glyph")}
    groups["combined_pixel_scopes"] = collision_scopes(groups["combined_identical_pixels"])
    groups["candidate_glyph_scopes"] = collision_scopes(groups["candidate_shared_glyph"])
    if groups["combined_pixel_scopes"]["same_character_different_pids"]:
        audit.flag("same_character_different_pids_identical_pixels",
                   groups["combined_pixel_scopes"]["same_character_different_pids"])
    existing_risks = []
    for path, info in before.items():
        target_size = 150 if "/potentials/" in path else 65
        if (info["width"], info["height"]) != (target_size, target_size) or not info["nonempty"]:
            existing_risks.append({"path": path, "width": info["width"], "height": info["height"],
                                   "nonempty": info["nonempty"]})
    audit.flag("in_game_recognition", "No held-out game-screen matching test was performed by this file audit.")
    audit.flag("face_variant_appearance", "Skin/variant identity must not be treated as game-screen appearance validation.")
    counts = Counter(row["status"] for row in audit.checks)
    status = "fail" if counts["fail"] else "pending" if counts["pending"] else "pass"
    report = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "artifact_status": status, "recognition_status": "not_game_verified", "registration_status": "not_registered",
              "baseline": str(baseline_path), "production_counts": current["counts"],
              "expected_potential_count": len(baseline["expected_missing_potentials"]),
              "expected_face_count": len(baseline["expected_missing_faces"]),
              "decoded_potential_count": len(potentials), "decoded_face_count": len(faces),
              "decoded_face_variant_count": len(variants),
              "decoded_control_count": len(controls),
              "check_counts": dict(counts), "checks": audit.checks, "flags": audit.flags,
              "collision_groups": groups, "existing_production_image_risks": existing_risks,
              "manifest_receipts": audit.manifest_receipts,
              "source_tree_receipts": {key: {k: v for k, v in tree.items() if k != "files"} for key, tree in audit.source_trees.items()},
              "potential_files": potentials, "face_files": faces, "face_variant_files": variants,
              "control_files": controls}
    report_path = output / "verification-report.json"
    write_json(report_path, report)
    print(json.dumps({k: report[k] for k in ("artifact_status", "recognition_status", "production_counts",
                                           "decoded_potential_count", "decoded_face_count", "check_counts")}, ensure_ascii=False))
    print("report=" + str(report_path))
    return 1 if status == "fail" else 2 if status == "pending" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("verification error: " + str(exc), file=sys.stderr)
        sys.exit(1)
