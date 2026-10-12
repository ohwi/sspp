"""Generate an unregistered full pinned-catalog synthetic asset set.

Uses the frozen r3 geometry, verified public sprites and catalog role arrays.
Never overwrites existing output, application images, model weights or photos.
--offline forbids downloads. --validate-cache verifies cached source coverage
without creating output; use --cache-dir to add the generated source cache.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import cv2
import numpy as np
import generate_potential_templates as gen
from calibrate_potential_templates import corner_region, render

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
EXPECTED_WITHIN_ROLE_PAIRS = {
    ("coronis", "511829", "511832"), ("nanoha", "511927", "511930"),
    ("ann", "512310", "512313"), ("caramel", "514729", "514732"),
}


def pixel_sha(image):
    prefix = json.dumps([list(image.shape), str(image.dtype)]).encode("ascii") + b"\0"
    return gen.sha256(prefix + image.tobytes())


def index_cache(roots):
    """Content addressing also recognizes legacy cache filenames with prefixes."""
    indexed = {}
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for suffix in ("*.png", "*.json"):
            for path in sorted(root.rglob(suffix)):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                raw = path.read_bytes()
                indexed.setdefault(gen.git_blob(raw), path)
    return indexed


def role_map(character):
    schema = character["preset_schema"]
    roles = {
        "main": schema["MasterSpecificPotentialIds"] + schema["MasterNormalPotentialIds"],
        "assist": schema["AssistSpecificPotentialIds"] + schema["AssistNormalPotentialIds"],
        "common": schema["CommonPotentialIds"],
    }
    if [len(roles[key]) for key in ("main", "assist", "common")] != [13, 13, 3]:
        raise ValueError("Expected original 13/13/3 role schema")
    flattened = [str(pid) for values in roles.values() for pid in values]
    if len(set(flattened)) != 29 or set(flattened) != set(character["potentials"]):
        raise ValueError("Catalog role arrays and potential IDs disagree")
    return roles, {str(pid): role for role, ids in roles.items() for pid in ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=WORK / "synthetic-validation-v1/assets")
    parser.add_argument("--audit-dir", type=Path, default=WORK / "asset-audit")
    parser.add_argument("--catalog", type=Path, default=ROOT / "app/resources/characters.json")
    parser.add_argument("--layout", type=Path, default=WORK / "calibration-v2-r3/fitted-layout.json")
    parser.add_argument("--cache-dir", type=Path, action="append", default=[])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--validate-cache", action="store_true")
    args = parser.parse_args()
    output, audit = args.output_dir.resolve(), args.audit_dir.resolve()
    caches = [audit, WORK / "generated-templates/sources/sstoy",
              WORK / "calibration-v2-r3/sources/sstoy"] + [p.resolve() for p in args.cache_dir]
    if not args.validate_cache:
        if output.exists():
            parser.error("Fresh output required; existing output is never overwritten")
        for protected in [ROOT, audit] + caches + [args.layout.resolve().parent]:
            if output == protected or protected in output.parents:
                parser.error("Output must be outside repository and protected input/cache directories")
    catalog = gen.read_json(args.catalog)
    tree_data = gen.read_json(audit / "db-coverage/sstoy_tree.json")
    if tree_data["sha"] != gen.SOURCE_REVISION or tree_data.get("truncated"):
        raise ValueError("Expected complete pinned SSToy source tree")
    tree = {row["path"]: row for row in tree_data["tree"]}
    cache = index_cache(caches)
    data_paths = {"Item.json": "public/data/Item.json", "Potential.json": "public/data/Potential.json",
                  "CharPotential.json": "public/data/CharPotential.json", "KR_Item.json": "public/data/KR/Item.json"}
    data, data_bytes = {}, {}
    for name, source_path in data_paths.items():
        source = cache.get(tree[source_path]["sha"])
        if source is None:
            raise ValueError(f"Pinned metadata missing from local audit cache: {source_path}")
        raw = source.read_bytes()
        if gen.git_blob(raw) != tree[source_path]["sha"]:
            raise ValueError("Metadata changed during cache indexing")
        data[name], data_bytes[source_path] = json.loads(raw), raw
    item, potential, char_potential, names = (data[name] for name in data_paths)
    rows, char_roles = [], {}
    for cid, character in sorted(catalog["characters"].items(), key=lambda pair: int(pair[0])):
        key = character["key"]
        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise ValueError("Unsafe character key")
        roles, by_pid = role_map(character)
        for field, values in character["preset_schema"].items():
            if char_potential[cid][field] != values:
                raise ValueError("Catalog schema differs from pinned native array order")
        char_roles[key] = {"character_id": int(cid), **roles,
                           "main_active": roles["main"] + roles["common"],
                           "assist_active": roles["assist"] + roles["common"]}
        for pid in sorted(character["potentials"], key=int):
            info, card = potential[pid], item[pid]
            if info["CharId"] != int(cid) or info["Corner"] not in (0, 1, 2, 3):
                raise ValueError("Potential/character mapping mismatch")
            name = names[card["Title"]]
            if name != character["potential_names"][pid]:
                raise ValueError("Catalog title differs from pinned Korean Item title")
            rows.append({"potential_id": int(pid), "character_id": int(cid), "character_key": key,
                         "name_ko": name, "role": by_pid[pid], "stype": card["Stype"],
                         "rarity": card["Rarity"], "corner": info["Corner"],
                         "glyph_path": gen.glyph_path(card), "background_variant": gen.background_variant(card)})
    if len(char_roles) != 40 or len(rows) != 1160:
        raise ValueError("This experiment is pinned to the 40-character/1160-PID catalog")
    glyphs = {row["glyph_path"] for row in rows}
    paths = glyphs | {gen.sprite_path(f"rare_vestige_card_s_{v}.png") for v in (4, 5, 6)}
    paths |= {gen.sprite_path(f"Potential_{shape}_{suffix}.png")
              for shape in gen.CORNER_NAMES.values() for suffix in ("A", "B")}
    if any(path not in tree or tree[path]["type"] != "blob" for path in paths):
        raise ValueError("Required sprite absent from pinned tree")
    available = {path for path in paths if tree[path]["sha"] in cache}
    print(f"Catalog 40/1160; glyphs {len(glyphs)}; cached glyphs {sum(p in available for p in glyphs)}; missing glyphs {sum(p not in available for p in glyphs)}", flush=True)
    if args.validate_cache:
        missing = sorted(paths - available)
        print(json.dumps({"status": "cache_complete" if not missing else "cache_incomplete",
                          "sprite_sources": len(paths), "cached_sources": len(available),
                          "pinned_metadata_sources": len(data_paths), "missing_sources": missing}, ensure_ascii=False))
        if missing:
            raise SystemExit(2)
        return
    if args.offline and available != paths:
        raise ValueError("Offline source cache is incomplete; no output created")
    layout_raw = args.layout.read_bytes()
    layout = json.loads(layout_raw)["layout"]
    production_before = gen.snapshot(ROOT / "app/resources/templates")
    model_before = {str(p): gen.sha256(p.read_bytes()) for p in (ROOT / "app/resources/models").glob("*") if p.is_file()}
    output.mkdir(parents=True, exist_ok=False)
    sources, images = {}, {}
    def obtain(source_path):
        local = cache.get(tree[source_path]["sha"])
        if local:
            raw = local.read_bytes()
            if gen.git_blob(raw) != tree[source_path]["sha"]:
                raise ValueError("Cached source changed")
            path = output / "sources/sstoy" / source_path
            gen.immutable_write(path, raw)
            receipt = {"repository": gen.SOURCE_REPOSITORY, "revision": gen.SOURCE_REVISION,
                       "path": source_path, "url": f"https://raw.githubusercontent.com/{gen.SOURCE_REPOSITORY}/{gen.SOURCE_REVISION}/{source_path}",
                       "git_blob_sha": gen.git_blob(raw), "sha256": gen.sha256(raw), "blob_verified": True,
                       "local_path": str(path), "relative_path": path.relative_to(output).as_posix(), "bytes": len(raw)}
            return source_path, raw, receipt
        return gen.fetch_source(source_path, tree, audit, output, args.offline)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for index, (path, raw, receipt) in enumerate(executor.map(obtain, sorted(paths | set(data_paths.values()))), 1):
            sources[gen.source_id(path)] = receipt
            if path in paths:
                images[path] = gen.decode(raw)
            if index % 50 == 0:
                print(f"Verified sources {index}/{len(paths) + len(data_paths)}", flush=True)
    entries = []
    groups, exclusive, active = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        image, used_layout = render(row, images, layout)
        ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        if not ok:
            raise ValueError("Candidate PNG encoding failed")
        raw, canonical = encoded.tobytes(), pixel_sha(image)
        pid, key = str(row["potential_id"]), row["character_key"]
        relative = f"potentials/{key}/{pid}.png"
        gen.immutable_write(output / relative, raw)
        meta = {"corner": row["corner"], "stype": row["stype"], "rarity": row["rarity"],
                "glyph": row["glyph_path"], "glyph_region": [0, 0, 150, 105],
                "corner_region": corner_region(used_layout, image.shape[0]),
                "layout": used_layout, "method": "aligned_components_v2"}
        refs = [gen.source_id(row["glyph_path"]), gen.source_id(gen.sprite_path(f"rare_vestige_card_s_{row['background_variant']}.png"))]
        if row["corner"]:
            refs += [gen.source_id(gen.sprite_path(f"Potential_{gen.CORNER_NAMES[row['corner']]}_{s}.png")) for s in ("A", "B")]
        entry = {"character_id": row["character_id"], "character_key": key, "potential_id": pid,
                 "name_ko": row["name_ko"], "role": row["role"], "meta": meta,
                 "relative_path": relative, "template_sha256": gen.sha256(raw),
                 "canonical_pixel_sha256": canonical, "width": int(image.shape[1]), "height": int(image.shape[0]),
                 "channels": 3, "source_refs": refs, "status": "candidate_not_registered"}
        entries.append(entry)
        groups[(key, canonical)].append(pid)
        exclusive[(key, row["role"], canonical)].append(pid)
        for role in ("main", "assist") if row["role"] == "common" else (row["role"],):
            active[(key, role, canonical)].append(pid)
    def ambiguity(mapping, with_role):
        return [{"character_key": key[0], **({"role": key[1]} if with_role else {}),
                 "canonical_pixel_sha256": key[-1], "potential_ids": sorted(values, key=int)}
                for key, values in sorted(mapping.items()) if len(values) > 1]
    before_role, within_role, active_role = ambiguity(groups, False), ambiguity(exclusive, True), ambiguity(active, True)
    observed = {(row["character_key"], *row["potential_ids"]) for row in within_role}
    manifest = {"calibration_schema_version": 2, "status": "candidate_not_registered",
                "catalog_scope": "Pinned DB40 only; not a claim about the current live server roster",
                "entries": entries, "entry_count": len(entries), "character_count": len(char_roles),
                "source_revision": gen.SOURCE_REVISION, "sources": sources,
                "char_roles": char_roles, "glyph_source_count": len(glyphs),
                "catalog_sha256": gen.sha256(args.catalog.read_bytes()), "layout_sha256": gen.sha256(layout_raw),
                "pixel_sha_spec": "SHA256(JSON([decoded_shape,dtype])+NUL+OpenCV BGR uint8 pixels)",
                "visual_ambiguities": {"before_role": before_role, "within_role": within_role,
                                       "active_role_pools": active_role,
                                       "expected_four_same_role_pairs_confirmed": observed == EXPECTED_WITHIN_ROLE_PAIRS},
                "limits": ["Synthetic assets remain unregistered; app and models unchanged.",
                           "Normal geometry was fitted on three characters; pink inherited a prior calibration.",
                           "Synthetic integrity/benchmark results do not establish live-screen matching.",
                           "Identical visual candidates must remain unknown without additional evidence."]}
    after = gen.snapshot(ROOT / "app/resources/templates")
    if after != production_before or any(gen.sha256(Path(p).read_bytes()) != value for p, value in model_before.items()):
        raise ValueError("Protected production images/models changed")
    manifest["production_unchanged"] = True
    manifest["models_unchanged"] = True
    gen.write_json(output / "potential-manifest.json", manifest)
    print(f"Generated {len(entries)} unregistered PNGs; ambiguity groups before role={len(before_role)}, within role={len(within_role)}, active role={len(active_role)}", flush=True)
    print(f"Four expected same-role pairs confirmed: {observed == EXPECTED_WITHIN_ROLE_PAIRS}", flush=True)
    print(f"manifest_sha256={gen.sha256((output / 'potential-manifest.json').read_bytes())}", flush=True)


if __name__ == "__main__":
    main()
