"""Generate unregistered potential recognition candidates from pinned public sprites.

The default output is a scratch directory outside this repository. No application
templates or matching thresholds are changed. Background, glyph and Corner layers
are transformed separately; the final image is not a resized database card.
Requires the application's numpy and opencv-python packages. Network downloads
are restricted to raw.githubusercontent.com/JforPlay/sstoy at SOURCE_REVISION.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.request

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REPOSITORY = "JforPlay/sstoy"
SOURCE_REVISION = "34f57606015277cf9da76a2c7cc14fa1b68ccbbf"
ASSETS_REVISION = "a970adc8ae875ffbfbad98a138ba569402959b4b"
CORNER_NAMES = {1: "Diamond", 2: "Triangle", 3: "Round"}
CORNER_COLORS = {1: "9b77e3", 2: "db8104"}
METHOD_VERSION = 1


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def immutable_write(path, raw):
    """An identical rerun is allowed; differing existing resources are refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"Refusing to overwrite different existing file: {path}")
        return
    # Exclusive creation also prevents simultaneous generators overwriting data.
    with path.open("xb") as stream:
        stream.write(raw)


def write_json(path, data):
    immutable_write(path, (json.dumps(data, ensure_ascii=False, indent=2,
                                     sort_keys=True) + "\n").encode("utf-8"))


def decode(raw, flag=cv2.IMREAD_UNCHANGED):
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), flag)
    if image is None or image.dtype != np.uint8:
        raise ValueError("Source cannot be decoded as an 8-bit image")
    if flag == cv2.IMREAD_UNCHANGED:
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
        elif image.shape[2] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        if image.shape[2] != 4:
            raise ValueError("Expected a three/four-channel source image")
    return image


def snapshot(templates):
    entries = {p.relative_to(templates).as_posix(): sha256(p.read_bytes())
               for p in sorted(templates.rglob("*.png"))}
    return {"file_count": len(entries),
            "sha256": sha256(json.dumps(entries, sort_keys=True).encode("utf-8"))}


def background_variant(item):
    if item["Stype"] == 42:
        return 6
    if item["Stype"] != 41 or item["Rarity"] not in (1, 2):
        raise ValueError(f"Unsupported potential item style: {item['Id']}")
    return {1: 5, 2: 4}[item["Rarity"]]


def sprite_path(name):
    if not re.fullmatch(r"[A-Za-z0-9_]+\.png", name):
        raise ValueError("Unsafe sprite basename")
    return "public/assets/skill_icons/" + name


def glyph_path(item):
    return sprite_path(item["Icon"].rsplit("/", 1)[-1] + "_A.png")


def source_id(path):
    return "sstoy:" + path


def fetch_source(path, tree, audit, output, offline=False):
    expected = tree[path]
    if expected["type"] != "blob":
        raise ValueError(f"Not a pinned source blob: {path}")
    local = output / "sources" / "sstoy" / path
    probes = [local, audit / "preset-sources" / Path(path).name,
              audit / Path(path).name]
    if path.startswith("public/data/"):
        data_name = path[len("public/data/"):].replace("/", "_")
        probes.append(audit / data_name)
    raw = None
    for probe in probes:
        if probe.exists():
            candidate = probe.read_bytes()
            if git_blob(candidate) == expected["sha"]:
                raw = candidate
                break
            if probe == local:
                raise ValueError(f"Cached source hash mismatch: {local}")
    url = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_REVISION}/{path}"
    if raw is None:
        if offline:
            raise ValueError(f"Source unavailable offline: {path}")
        request = urllib.request.Request(url, headers={"User-Agent": "sspp-candidate-generator/1"})
        last_error = None
        for _ in range(2):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.geturl() != url:
                        raise ValueError("Unexpected source redirect")
                    raw = response.read(2_000_001)
                break
            except (OSError, TimeoutError) as error:
                last_error = error
        if raw is None:
            raise RuntimeError(f"Could not fetch {path}: {last_error}")
    if len(raw) > 2_000_000 or git_blob(raw) != expected["sha"]:
        raise ValueError(f"Pinned Git blob mismatch: {path}")
    immutable_write(local, raw)
    receipt = {
        "repository": SOURCE_REPOSITORY, "revision": SOURCE_REVISION,
        "path": path, "url": url, "sha256": sha256(raw),
        "git_blob_sha": git_blob(raw), "blob_verified": True,
        "local_path": str(local.resolve()),
        "relative_path": local.relative_to(output).as_posix(), "bytes": len(raw),
    }
    return path, raw, receipt


def bg_image(sprite, transform):
    sx, sy, ty = transform["scale_x"], transform["scale_y"], transform["top_y"]
    matrix = np.float32([[sx, 0, 75 - sprite.shape[1] * sx / 2], [0, sy, ty]])
    return cv2.warpAffine(sprite[:, :, :3], matrix, (150, 150),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def add_layer(canvas, sprite, transform, tint=None):
    scale, cx, cy = transform["scale"], *transform["center"]
    matrix = np.float32([[scale, 0, cx - sprite.shape[1] * scale / 2],
                         [0, scale, cy - sprite.shape[0] * scale / 2]])
    layer = cv2.warpAffine(sprite, matrix, (150, 150), flags=cv2.INTER_LINEAR)
    alpha = layer[:, :, 3:4].astype(np.float32) / 255
    rgb = layer[:, :, :3].astype(np.float32)
    if tint:
        color = np.array([int(tint[4:6], 16), int(tint[2:4], 16),
                          int(tint[0:2], 16)], np.float32) / 255
        rgb *= color
    return np.rint(rgb * alpha + canvas * (1 - alpha)).astype(np.uint8)


def ncc(first, second):
    a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY).astype(np.float32).ravel()
    b = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY).astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denominator) if denominator else 0.0


def render(row, images, layout):
    variant = str(row["background_variant"])
    canvas = bg_image(images[sprite_path(f"rare_vestige_card_s_{variant}.png")],
                      layout["backgrounds"][variant])
    canvas = add_layer(canvas, images[row["glyph_path"]], layout["glyph"])
    if row["corner"]:
        shape = CORNER_NAMES[row["corner"]]
        canvas = add_layer(canvas, images[sprite_path(f"Potential_{shape}_B.png")],
                           layout["corner"], CORNER_COLORS[row["rarity"]])
        canvas = add_layer(canvas, images[sprite_path(f"Potential_{shape}_A.png")],
                           layout["corner"])
    return canvas


def fit_background(sprite, references):
    # Non-white pixels common to independent glyphs isolate the shared background.
    stack = np.stack(references)
    mask = np.all(np.min(stack, axis=3) < 230, axis=0)
    mask[110:] = False  # Role badge is fitted separately.
    yy, xx = np.where(mask)
    yy, xx = yy[::8], xx[::8]
    if len(xx) < 50:
        raise ValueError("Not enough independent background samples")
    desired = np.median(stack[:, yy, xx], axis=0).astype(np.float32)
    best = (float("inf"), None)
    for sx in np.arange(1.2, 2.201, .1):
        for sy in np.arange(1.0, 2.101, .1):
            for ty in range(-110, -29, 10):
                mx = ((xx - 75 + sprite.shape[1] * sx / 2) / sx).astype(np.float32)[None, :]
                my = ((yy - ty) / sy).astype(np.float32)[None, :]
                sampled = cv2.remap(sprite[:, :, :3], mx, my, cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE)[0]
                error = float(np.mean((sampled.astype(np.float32) - desired) ** 2))
                if error < best[0]:
                    best = error, {"scale_x": round(float(sx), 4),
                                   "scale_y": round(float(sy), 4), "top_y": float(ty),
                                   "center_x": 75.0}
    return {**best[1], "fit_rgb_mse": best[0], "fit_pixel_count": len(xx)}


def calibrate(train, heldout, images):
    layout = {"backgrounds": {}, "glyph": {"scale": .59, "center": [83., 75.]},
              "corner": {"scale": .6, "center": [75., 131.]}}
    for variant in (4, 5, 6):
        refs = [row["reference"] for row in train if row["background_variant"] == variant]
        layout["backgrounds"][str(variant)] = fit_background(
            images[sprite_path(f"rare_vestige_card_s_{variant}.png")], refs)
    # One common glyph transform is selected across all seven style/Corner groups.
    best = (-2., None)
    for scale in np.arange(.50, .681, .02):
        for cx in (73., 78., 83., 88.):
            for cy in (69., 74., 79.):
                layout["glyph"] = {"scale": round(float(scale), 4), "center": [cx, cy]}
                score = float(np.mean([ncc(render(row, images, layout), row["reference"])
                                       for row in train]))
                if score > best[0]:
                    best = score, dict(layout["glyph"])
    layout["glyph"] = best[1]
    normal = [row for row in train if row["corner"]]
    best_corner = (-2., None)
    for scale in (.45, .50, .55, .60, .65):
        for cx in (73., 77., 81.):
            for cy in (123., 127., 131., 135.):
                layout["corner"] = {"scale": scale, "center": [cx, cy]}
                scores = []
                for row in normal:
                    image = render(row, images, layout)
                    scores.append(.7 * ncc(image[105:], row["reference"][105:])
                                  + .3 * ncc(image, row["reference"]))
                score = float(np.mean(scores))
                if score > best_corner[0]:
                    best_corner = score, dict(layout["corner"])
    layout["corner"] = best_corner[1]
    evaluation = []
    for split, rows in (("fit", train), ("held_out", heldout)):
        for row in rows:
            candidate = render(row, images, layout)
            evaluation.append({"potential_id": row["potential_id"], "split": split,
                               "background_variant": row["background_variant"],
                               "corner": row["corner"],
                               "reference_sha256": row["reference_sha256"],
                               "exact_150_grayscale_ncc": ncc(candidate, row["reference"]),
                               "badge_region_ncc": ncc(candidate[105:], row["reference"][105:])})
    return {"method_version": METHOD_VERSION, "layout": layout, "evaluation": evaluation,
            "fit_method": "bounded grid; common glyph/Corner affine transform; per-rarity background affine transform",
            "comparison_method": "direct grayscale normalized correlation at identical 150x150 size; no scale or shift search",
            "limits": ["Held-out legacy crops test geometry transfer, not current game matching.",
                       "Source images are sprites without prefab positions; positions are empirically approximated.",
                       "New characters and normal Corner layouts have no current-client screenshot validation.",
                       "No matcher thresholds are modified; candidates remain unregistered."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=ROOT.parent / "asset-audit")
    parser.add_argument("--output-dir", type=Path, default=ROOT.parent / "generated-templates")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    audit, output = args.audit_dir.resolve(), args.output_dir.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Candidate output must be outside the application repository")
    templates = ROOT / "app/resources/templates"
    before = snapshot(templates)
    catalog_raw = (ROOT / "app/resources/characters.json").read_bytes()
    catalog = json.loads(catalog_raw)
    tree_data = read_json(audit / "db-coverage/sstoy_tree.json")
    assets_tree_data = read_json(audit / "db-coverage/ssassets_tree.json")
    for data, revision in ((tree_data, SOURCE_REVISION), (assets_tree_data, ASSETS_REVISION)):
        if data["sha"] != revision or data.get("truncated"):
            raise ValueError("Expected a complete pinned Git source tree")
    tree = {row["path"]: row for row in tree_data["tree"]}
    assets_tree = {row["path"]: row for row in assets_tree_data["tree"]}
    # These mapping bytes must also match the public pinned tree before use.
    mapping_sources = {}
    mappings = {}
    for name in ("Potential.json", "Item.json", "KR/Item.json"):
        path, raw, receipt = fetch_source("public/data/" + name, tree, audit, output, args.offline)
        mapping_sources[source_id(path)] = receipt
        mappings[name] = json.loads(raw)
    potentials, items, names = mappings["Potential.json"], mappings["Item.json"], mappings["KR/Item.json"]
    rows, missing, groups = [], [], {}
    for char_id, character in sorted(catalog["characters"].items(), key=lambda pair: int(pair[0])):
        key = character["key"]
        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise ValueError("Unsafe character key")
        for pid in sorted(character["potentials"], key=int):
            potential, item = potentials[pid], items[pid]
            if potential["CharId"] != int(char_id) or int(pid) <= 0:
                raise ValueError(f"Potential/character mismatch: {pid}")
            corner, variant = potential["Corner"], background_variant(item)
            if corner not in (0, 1, 2, 3) or (item["Stype"] == 42 and corner != 0):
                raise ValueError(f"Unsupported Corner: {pid}")
            db_path = f"potential/{pid}.webp"
            if db_path not in assets_tree or glyph_path(item) not in tree:
                raise ValueError(f"Pinned source coverage missing: {pid}")
            row = {"potential_id": int(pid), "character_id": int(char_id), "character_key": key,
                   "name_ko": names[item["Title"]], "stype": item["Stype"], "rarity": item["Rarity"],
                   "corner": corner, "background_variant": variant, "glyph_path": glyph_path(item),
                   "database_card": {"repository": "AutumnVN/ssassets", "revision": ASSETS_REVISION,
                                     "path": db_path, "git_blob_sha": assets_tree[db_path]["sha"],
                                     "status": "pinned_tree_presence_only_not_pixel_input"}}
            reference_path = templates / "potentials" / key / f"{pid}.png"
            rows.append(row)
            if reference_path.exists():
                group = (variant, corner)
                raw = reference_path.read_bytes()
                reference = decode(raw, cv2.IMREAD_COLOR)
                if reference.shape != (150, 150, 3):
                    raise ValueError(f"Unexpected legacy template dimensions: {reference_path}")
                row.update(reference=reference, reference_sha256=sha256(raw))
                groups.setdefault(group, []).append(row)
            else:
                missing.append(row)
    if len(rows) != 1160 or len(missing) != 287 or len(catalog["characters"]) != 40:
        raise ValueError(f"Coverage differs from approved snapshot: {len(rows)} total, {len(missing)} missing")
    recorded = read_json(audit / "db-coverage/coverage-audit.json")
    expected = {int(pid) for character in recorded["characters"] for pid in character["missing_potential_ids"]}
    if expected != {row["potential_id"] for row in missing}:
        raise ValueError("Independent coverage audit does not match derived missing IDs")
    train, heldout = [], []
    for group, pool in sorted(groups.items()):
        pool = sorted(pool, key=lambda row: row["potential_id"])
        # Deterministic disjoint references from seven rarity/Corner groups.
        train.extend(pool[index] for index in (0, len(pool) // 3, len(pool) // 2))
        heldout.append(pool[2 * len(pool) // 3])
    controls = train + heldout
    paths = {row["glyph_path"] for row in missing + controls}
    paths.update(sprite_path(f"rare_vestige_card_s_{variant}.png") for variant in (4, 5, 6))
    paths.update(sprite_path(f"Potential_{shape}_{suffix}.png")
                 for shape in CORNER_NAMES.values() for suffix in ("A", "B"))
    print(f"Coverage verified: 1160 IDs, 873 production potentials, 287 missing; fetching {len(paths)} sprite sources", flush=True)
    sources, images = dict(mapping_sources), {}
    def download(path):
        return fetch_source(path, tree, audit, output, args.offline)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for index, (path, raw, receipt) in enumerate(executor.map(download, sorted(paths)), 1):
            sources[source_id(path)] = receipt
            images[path] = decode(raw)
            if index % 40 == 0:
                print(f"Verified source images: {index}/{len(paths)}", flush=True)
    calibration_path = output / "potential-calibration.json"
    signature = {"catalog_sha256": sha256(catalog_raw), "production": before,
                 "method_version": METHOD_VERSION,
                 "sprite_sha256": {key: source["sha256"] for key, source in sources.items()}}
    if calibration_path.exists():
        calibration = read_json(calibration_path)
        if calibration["input_signature"] != signature:
            raise ValueError("Existing calibration belongs to different inputs/method")
    else:
        print("Fitting common glyph/Corner geometry and three background transforms on 21 legacy references", flush=True)
        calibration = calibrate(train, heldout, images)
        calibration["input_signature"] = signature
        write_json(calibration_path, calibration)
    layout = calibration["layout"]
    print(f"Common layout: glyph={layout['glyph']}; Corner={layout['corner']}", flush=True)
    manifest_rows, control_rows = [], []
    for category, selected, destination in (("potentials", missing, manifest_rows),
                                            ("controls", controls, control_rows)):
        for row in selected:
            image = render(row, images, layout)
            ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
            if not ok:
                raise ValueError("Cannot encode candidate PNG")
            raw = encoded.tobytes()
            relative = f"{category}/{row['character_key']}/{row['potential_id']}.png"
            immutable_write(output / relative, raw)
            refs = [source_id(row["glyph_path"]),
                    source_id(sprite_path(f"rare_vestige_card_s_{row['background_variant']}.png"))]
            if row["corner"]:
                shape = CORNER_NAMES[row["corner"]]
                refs.extend(source_id(sprite_path(f"Potential_{shape}_{suffix}.png")) for suffix in ("B", "A"))
            record = {key: value for key, value in row.items()
                      if key not in ("reference", "reference_sha256", "glyph_path")}
            record.update(relative_path=relative, sha256=sha256(raw), width=150, height=150,
                          channels=3, status="candidate_not_registered", source_refs=refs,
                          transform={"background": layout["backgrounds"][str(row["background_variant"])],
                                     "glyph": layout["glyph"],
                                     "corner": layout["corner"] if row["corner"] else None,
                                     "corner_tint_rgb": "#" + CORNER_COLORS[row["rarity"]] if row["corner"] else None},
                          validation="unverified_current_client")
            destination.append(record)
    after = snapshot(templates)
    if after != before:
        raise ValueError("Production images changed during candidate generation")
    manifest = {"schema_version": 1, "status": "candidate_not_registered",
                "generator": "scripts/generate_potential_templates.py", "method_version": METHOD_VERSION,
                "source_revision": SOURCE_REVISION, "sources": sources, "missing_count": len(missing),
                "files": manifest_rows, "controls": control_rows,
                "production_before": before, "production_after": after,
                "calibration": {"relative_path": "potential-calibration.json",
                                "sha256": sha256(calibration_path.read_bytes())},
                "limits": calibration["limits"]}
    write_json(output / "potential-manifest.json", manifest)
    values = [row["exact_150_grayscale_ncc"] for row in calibration["evaluation"] if row["split"] == "held_out"]
    print(f"Generated {len(manifest_rows)} missing candidates and {len(control_rows)} controls; production unchanged", flush=True)
    print(f"Held-out legacy exact-size NCC range {min(values):.4f}..{max(values):.4f}; no current-client claim", flush=True)
    print(str(output / "potential-manifest.json"), flush=True)


if __name__ == "__main__":
    main()
