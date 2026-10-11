"""Fit v2 potential candidates using the frozen even-index screenshot subset only.

Outputs remain outside app/resources. Existing screenshots, v1 candidates and
production images are immutable inputs. Test screenshots are loaded only after
the fitted layout is written; they are regression checks on previously inspected
images, not fresh evidence of generalization. No network access is required.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
sys.path.insert(0, str(ROOT / "app"))

import cv2
import numpy as np
from config import REFERENCE_WIDTH, ROIS
from src.card_matcher import match_card
from src.load_resolution import get_capture_area
import generate_potential_templates as gen

TARGET_CHARACTERS = {"allie", "donna", "suntide_willow"}


def fingerprint(path):
    return gen.sha256(Path(path).read_bytes())


def load_roi(input_row, index):
    raw = Path(input_row["frozen"]).read_bytes()
    image = gen.decode(raw, cv2.IMREAD_COLOR)
    # Same fixed client crop as the saved-screenshot baseline. The first five
    # inputs were already manually cropped and retain their measured dimensions.
    if image.shape[:2] == (932, 1602):
        image = image[31:931, 1:1601]
    geo = {"x": 0, "y": 0, "w": image.shape[1], "h": image.shape[0]}
    area = get_capture_area(geo, ROIS[index], None)
    patch = image[area["top"]:area["top"] + area["height"],
                  area["left"]:area["left"] + area["width"]]
    scale = REFERENCE_WIDTH / geo["w"]
    if scale != 1:
        patch = cv2.resize(patch, (round(patch.shape[1] * scale), round(patch.shape[0] * scale)),
                           interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    return patch


def gray(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def aligned_score(template, roi):
    scores = cv2.matchTemplate(gray(roi), gray(template), cv2.TM_CCOEFF_NORMED)
    valid = np.isfinite(scores)
    if not np.any(valid):
        return -1., None
    y, x = np.unravel_index(np.argmax(np.where(valid, scores, -np.inf)), scores.shape)
    return float(scores[y, x]), (int(x), int(y))


def corner_region(layout, height=170):
    cx, cy = layout["corner"]["center"]
    half = int(np.ceil(34 * layout["corner"]["scale"])) + 3
    return [max(0, int(cx - half)), max(0, int(cy - half)),
            min(150, int(cx + half)), min(height, int(cy + half))]


def layer(canvas, sprite, transform, tint=None):
    scale, cx, cy = transform["scale"], *transform["center"]
    matrix = np.float32([[scale, 0, cx - sprite.shape[1] * scale / 2],
                         [0, scale, cy - sprite.shape[0] * scale / 2]])
    rgba = cv2.warpAffine(sprite, matrix, (canvas.shape[1], canvas.shape[0]), flags=cv2.INTER_LINEAR)
    rgb = rgba[:, :, :3].astype(np.float32)
    if tint:
        rgb *= np.array([int(tint[4:6], 16), int(tint[2:4], 16), int(tint[0:2], 16)], np.float32) / 255
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255
    return np.rint(rgb * alpha + canvas * (1 - alpha)).astype(np.uint8), rgba[:, :, 3]


def background(sprite, transform, height):
    sx, sy, top = transform["scale_x"], transform["scale_y"], transform["top_y"]
    matrix = np.float32([[sx, 0, 75 - sprite.shape[1] * sx / 2], [0, sy, top]])
    return cv2.warpAffine(sprite[:, :, :3], matrix, (150, height),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def anchor(template, roi):
    search = cv2.matchTemplate(gray(roi), gray(template)[:105], cv2.TM_CCOEFF_NORMED)
    search = search[:roi.shape[0] - template.shape[0] + 1]
    if not search.size or not np.any(np.isfinite(search)):
        return None
    y, x = np.unravel_index(np.argmax(np.where(np.isfinite(search), search, -np.inf)), search.shape)
    return roi[y:y + template.shape[0], x:x + template.shape[1]]


def fit_background_pixels(rows, images, layout, variant):
    samples = []
    for row in rows:
        candidate, _ = render(row, images, layout)
        patch = anchor(candidate, row["roi"])
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        empty = np.zeros(candidate.shape, np.uint8)
        _, glyph_alpha = layer(empty, images[row["glyph_path"]], layout["glyph"])
        selected = (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 70) & (glyph_alpha < 20)
        selected[:15] = False  # Exclude the recommendation ribbon at the top.
        if int(selected.sum()) >= 100:
            samples.append((patch, selected))
    if not samples:
        raise ValueError("No background-only color evidence in fit subset")
    sprite = images[gen.sprite_path(f"rare_vestige_card_s_{variant}.png")]
    best = (float("inf"), None)
    for sx in (1.6, 1.8, 2.0, 2.2, 2.4, 2.6):
        for sy in (1.2, 1.4, 1.6, 1.8, 2.0, 2.2):
            for top in (-100., -80., -60., -40.):
                transform = {"scale_x": sx, "scale_y": sy, "top_y": top, "center_x": 75.}
                candidate = background(sprite, transform, 170)
                errors = [float(np.mean((candidate[mask].astype(np.float32) - patch[mask]) ** 2))
                          for patch, mask in samples]
                value = float(np.mean(errors))
                if value < best[0]:
                    best = value, transform
    return best


def objective(fit_rows, images, layout, include_corner=False):
    scores = []
    for row in fit_rows:
        candidate, _ = render(row, images, layout)
        # Fit using the same top-aligned glyph anchor as the live matcher. A
        # whole-card alignment during fit and glyph-only alignment at runtime
        # would place the Corner comparison in two different windows.
        patch = anchor(candidate, row["roi"])
        if patch is None:
            scores.append(-1.)
            continue
        score = gen.ncc(candidate, patch)
        if include_corner:
            x1, y1, x2, y2 = corner_region(layout)
            badge = gen.ncc(candidate[y1:y2, x1:x2], patch[y1:y2, x1:x2])
            # Fit against actual same-glyph counterexamples, not just a high
            # correlation for the correct reference. Only fit-label metadata is
            # used; no test observations or test PIDs enter this objective.
            alternatives = []
            for corner in (1, 2, 3):
                if corner != row["corner"]:
                    wrong, _ = render({**row, "corner": corner}, images, layout)
                    alternatives.append(gen.ncc(wrong[y1:y2, x1:x2], patch[y1:y2, x1:x2]))
            discrimination = badge - max(alternatives)
            score = .35 * score + .45 * badge + .20 * discrimination
        scores.append(score)
    return float(np.mean(scores))


def fit_layout(fit_rows, images, initial):
    layout = copy.deepcopy(initial)
    # Pink has no fit observations in the prescribed split. Reuse the previously
    # documented separate Willow calibration; never inspect test pink to fit it.
    layout["backgrounds"]["6"] = {"scale_x": 1.8, "scale_y": 1.6,
                                     "top_y": -60., "center_x": 75.}
    trace = []
    for variant in (4, 5):
        rows = [row for row in fit_rows if row["background_variant"] == variant]
        best = fit_background_pixels(rows, images, layout, variant)
        layout["backgrounds"][str(variant)] = best[1]
        trace.append({"stage": "background", "variant": variant, "fit_objective": best[0], "chosen": best[1]})
    best = (-2., None)
    for scale in (.50, .525, .55, .575, .60, .625, .65, .675, .70):
        for cx in (70., 75., 80., 85.):
            for cy in (65., 70., 75., 80.):
                layout["glyph"] = {"scale": scale, "center": [cx, cy]}
                value = objective(fit_rows, images, layout)
                if value > best[0]:
                    best = value, dict(layout["glyph"])
    layout["glyph"] = best[1]
    trace.append({"stage": "common_normal_glyph", "fit_objective": best[0], "chosen": best[1]})
    best = (-2., None)
    for scale in (.65, .70, .75, .80, .85, .90, .95, 1.0, 1.05):
        for cx in (70., 75., 80., 85.):
            for cy in (130., 135., 140., 145.):
                # Check actual alpha support, not the transparent sprite canvas.
                # The 170 px image preserves the entire A/B badge at runtime.
                supported = True
                for shape in gen.CORNER_NAMES.values():
                    for suffix in ("A", "B"):
                        sprite = images[gen.sprite_path(f"Potential_{shape}_{suffix}.png")]
                        yy, xx = np.where(sprite[:, :, 3] > 0)
                        low_y = cy + (yy.min() - sprite.shape[0] / 2) * scale
                        high_y = cy + (yy.max() - sprite.shape[0] / 2) * scale
                        low_x = cx + (xx.min() - sprite.shape[1] / 2) * scale
                        high_x = cx + (xx.max() - sprite.shape[1] / 2) * scale
                        supported = supported and low_x >= 0 and high_x < 150 and low_y >= 105 and high_y < 170
                if not supported:
                    continue
                layout["corner"] = {"scale": scale, "center": [cx, cy]}
                value = objective(fit_rows, images, layout, include_corner=True)
                if value > best[0]:
                    best = value, dict(layout["corner"])
    layout["corner"] = best[1]
    trace.append({"stage": "common_corner", "fit_objective": best[0], "chosen": best[1]})
    return layout, trace


def render(row, images, normal_layout):
    layout = normal_layout
    if row["stype"] == 42:
        layout = copy.deepcopy(normal_layout)
        layout["glyph"] = {"scale": .59, "center": [83., 75.]}
    height = 150 if row["stype"] == 42 else 170
    canvas = background(images[gen.sprite_path(f"rare_vestige_card_s_{row['background_variant']}.png")],
                        layout["backgrounds"][str(row["background_variant"])], height)
    canvas, _ = layer(canvas, images[row["glyph_path"]], layout["glyph"])
    if row["corner"]:
        shape = gen.CORNER_NAMES[row["corner"]]
        canvas, _ = layer(canvas, images[gen.sprite_path(f"Potential_{shape}_B.png")],
                          layout["corner"], gen.CORNER_COLORS[row["rarity"]])
        canvas, _ = layer(canvas, images[gen.sprite_path(f"Potential_{shape}_A.png")], layout["corner"])
    return canvas, layout


def allowed_pool(character, role):
    schema = character["preset_schema"]
    prefix = "Master" if role == "main" else "Assist"
    return set(schema[prefix + "SpecificPotentialIds"] + schema[prefix + "NormalPotentialIds"]
               + schema["CommonPotentialIds"])


def evaluate(input_rows, truth, pools, catalog, split):
    results = []
    counts = {"correct": 0, "wrong_confirmed": 0, "unrecognized": 0}
    roles = {"allie": "main", "donna": "assist", "suntide_willow": "assist"}
    characters = {row["key"]: row for row in catalog["characters"].values()}
    for input_row in input_rows:
        for expected in truth[input_row["id"]]:
            key = expected["character_key"]
            image = load_roi(input_row, expected["index"])
            # Both diagnostics skip face matching. The role diagnostic explicitly
            # assumes the user's supplied main/support ordering matches this run.
            for mode, allowed in (("known_character_no_role", None),
                                  ("known_character_known_role", allowed_pool(characters[key], roles[key]))):
                filename, score, details = match_card(image, key, pools, allowed)
                prediction = filename.removesuffix(".png") if filename else None
                status = "correct" if prediction == expected["potential_id"] else "unrecognized" if prediction is None else "wrong_confirmed"
                if mode == "known_character_known_role":
                    counts[status] += 1
                results.append({"split": split, "mode": mode, "image_id": input_row["id"],
                                "index": expected["index"], "character_key": key,
                                "expected_pid": expected["potential_id"], "predicted_pid": prediction,
                                "status": status, "score": score, "diagnostics": details})
    return {"counts_known_character_known_role": counts, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=WORK / "calibration-v2-r3")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Scratch output must be outside the application repository")
    validation, audit = WORK / "screenshot-validation", WORK / "asset-audit"
    inputs_path = validation / "inputs.json"
    inputs = sorted(gen.read_json(inputs_path)["files"], key=lambda row: row["id"])
    if len(inputs) != 16 or len({row["id"] for row in inputs}) != 16:
        raise ValueError("Expected the frozen sixteen-image set")
    fit_inputs, test_inputs = inputs[::2], inputs[1::2]
    truth = {row["id"]: row["cards"] for name in ("ground-truth-a.json", "ground-truth-b.json")
             for row in gen.read_json(validation / name)["files"]}
    catalog_path = ROOT / "app/resources/characters.json"
    catalog = gen.read_json(catalog_path)
    before = gen.snapshot(ROOT / "app/resources/templates")
    v1_manifest_path = WORK / "generated-templates/potential-manifest.json"
    v1_before = fingerprint(v1_manifest_path)
    v1 = gen.read_json(v1_manifest_path)
    split_record = {"schema_version": 1, "policy": "chronological 0-based even indices fit; odd indices test",
                    "fit_ids": [row["id"] for row in fit_inputs],
                    "test_ids": [row["id"] for row in test_inputs],
                    "source_manifest_sha256": fingerprint(inputs_path),
                    "input_sha256": {row["id"]: fingerprint(row["frozen"]) for row in inputs},
                    "ground_truth_sha256": {name: fingerprint(validation / name)
                                            for name in ("ground-truth-a.json", "ground-truth-b.json")},
                    "limits": "These screenshots were inspected in the prior audit; this split isolates v2 fitting only, not fresh generalization."}
    gen.write_json(output / "split.json", split_record)
    potential = gen.read_json(audit / "Potential.json")
    items = gen.read_json(audit / "Item.json")
    tree_payload = gen.read_json(audit / "db-coverage/sstoy_tree.json")
    if tree_payload["sha"] != gen.SOURCE_REVISION or tree_payload.get("truncated"):
        raise ValueError("Expected complete pinned source tree")
    tree = {row["path"]: row for row in tree_payload["tree"]}
    rows = []
    missing = {(row["character_key"], row["potential_id"]) for row in v1["files"]}
    for char_id, character in catalog["characters"].items():
        for pid in character["potentials"]:
            if (character["key"], int(pid)) not in missing and character["key"] not in TARGET_CHARACTERS:
                continue
            item, info = items[pid], potential[pid]
            rows.append({"potential_id": int(pid), "character_id": int(char_id),
                         "character_key": character["key"], "name_ko": character["potential_names"][pid],
                         "stype": item["Stype"], "rarity": item["Rarity"], "corner": info["Corner"],
                         "glyph_path": gen.glyph_path(item), "background_variant": gen.background_variant(item)})
    if len(rows) != 290 or sum(row["character_key"] in TARGET_CHARACTERS for row in rows) != 87:
        raise ValueError("Expected missing 287 plus three existing Willow controls")
    bypid = {str(row["potential_id"]): row for row in rows}
    fit_rows = []
    # No test screenshot pixels or test labels are used in the following fit.
    for input_row in fit_inputs:
        for label in truth[input_row["id"]]:
            row = dict(bypid[label["potential_id"]])
            row["roi"] = load_roi(input_row, label["index"])
            fit_rows.append(row)
    sources, images = {}, {}
    paths = {row["glyph_path"] for row in rows}
    paths.update(gen.sprite_path(f"rare_vestige_card_s_{variant}.png") for variant in (4, 5, 6))
    paths.update(gen.sprite_path(f"Potential_{shape}_{suffix}.png")
                 for shape in gen.CORNER_NAMES.values() for suffix in ("A", "B"))
    for path in sorted(paths):
        ref = gen.source_id(path)
        receipt = v1["sources"].get(ref)
        if receipt:
            raw = Path(receipt["local_path"]).read_bytes()
            if gen.git_blob(raw) != tree[path]["sha"] or gen.sha256(raw) != receipt["sha256"]:
                raise ValueError(f"Source receipt mismatch: {path}")
            local = output / "sources/sstoy" / path
            gen.immutable_write(local, raw)
            receipt = {**receipt, "local_path": str(local), "relative_path": local.relative_to(output).as_posix()}
        else:
            _, raw, receipt = gen.fetch_source(path, tree, audit, output, offline=True)
        images[path], sources[ref] = gen.decode(raw), receipt
    initial = gen.read_json(WORK / "generated-templates/potential-calibration.json")["layout"]
    print("Fitting 24 normal-card observations from eight even-index images only", flush=True)
    fitted_path = output / "fitted-layout.json"
    fit_signature = {"split_sha256": fingerprint(output / "split.json"),
                     "catalog_sha256": fingerprint(catalog_path), "v1_sha256": v1_before,
                     "method": "normal_background_pixels_full_badge_v2_r3"}
    if fitted_path.exists():
        fitted = gen.read_json(fitted_path)
        if fitted["input_signature"] != fit_signature:
            raise ValueError("Existing fitted layout input signature differs")
        layout = fitted["layout"]
    else:
        layout, trace = fit_layout(fit_rows, images, initial)
        fitted = {"calibration_schema_version": 2, "input_signature": fit_signature,
                  "layout": layout, "trace": trace, "fit_cards": len(fit_rows),
                  "specific_fit_cards": sum(row["stype"] == 42 for row in fit_rows),
                  "specific_policy": "inherit separate pre-v2 Willow pink layout; no pink fit observations",
                  "matcher_thresholds_tuned_on_test": False}
        gen.write_json(fitted_path, fitted)
    print(f"Frozen normal layout: {layout}", flush=True)
    entries, pools = [], {}
    for row in sorted(rows, key=lambda row: (row["character_key"], row["potential_id"])):
        image, used_layout = render(row, images, layout)
        ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        if not ok:
            raise ValueError("Cannot encode candidate")
        raw = encoded.tobytes()
        relative = f"potentials/{row['character_key']}/{row['potential_id']}.png"
        gen.immutable_write(output / relative, raw)
        meta = {"corner": row["corner"], "stype": row["stype"], "rarity": row["rarity"],
                "glyph": row["glyph_path"], "glyph_region": [0, 0, 150, 105],
                "corner_region": corner_region(used_layout, image.shape[0]), "layout": used_layout,
                "method": "aligned_components_v2"}
        refs = [gen.source_id(row["glyph_path"]),
                gen.source_id(gen.sprite_path(f"rare_vestige_card_s_{row['background_variant']}.png"))]
        if row["corner"]:
            refs.extend(gen.source_id(gen.sprite_path(f"Potential_{gen.CORNER_NAMES[row['corner']]}_{suffix}.png"))
                        for suffix in ("A", "B"))
        entries.append({"character_key": row["character_key"], "character_id": row["character_id"],
                        "potential_id": str(row["potential_id"]), "name_ko": row["name_ko"],
                        "meta": meta, "relative_path": relative, "template_sha256": gen.sha256(raw),
                        "source_refs": refs, "status": "candidate_not_registered",
                        "fit_status": "normal_geometry_fit_only" if row["stype"] == 41 else "pink_no_v2_fit_observation"})
        if row["character_key"] in TARGET_CHARACTERS:
            pools.setdefault(row["character_key"], {})[f"{row['potential_id']}.png"] = {"gray": gray(image), "bgr": image, "meta": meta}
    manifest = {"calibration_schema_version": 2, "status": "candidate_not_registered",
                "entry_count": len(entries), "missing_original_count": 287,
                "target_character_entry_count": 87, "entries": entries, "sources": sources,
                "split_sha256": fingerprint(output / "split.json"),
                "fitted_layout_sha256": fingerprint(fitted_path),
                "production_before": before, "v1_manifest_sha256": v1_before,
                "limits": ["No production registration or live capture in this generator.",
                           "Only three characters occur in the saved regression images.",
                           "The test subset was previously inspected; it is not fresh generalization evidence.",
                           "Pink has no v2 fit observations; its previous layout is inherited.",
                           "Same pixel pairs without a verified role constraint remain ambiguous."]}
    gen.write_json(output / "potential-manifest.json", manifest)
    # Test input pixels are first loaded here, after the chosen layout is frozen.
    fit_results = evaluate(fit_inputs, truth, pools, catalog, "fit")
    test_results = evaluate(test_inputs, truth, pools, catalog, "test")
    after = gen.snapshot(ROOT / "app/resources/templates")
    if before != after or fingerprint(v1_manifest_path) != v1_before:
        raise ValueError("Protected production/v1 inputs changed")
    report = {"calibration_schema_version": 2, "scope": "known-character card diagnostic only",
              "fit": fit_results, "test": test_results, "production_unchanged": before == after,
              "v1_manifest_unchanged": fingerprint(v1_manifest_path) == v1_before,
              "end_to_end": False, "matcher_sha256": fingerprint(ROOT / "app/src/card_matcher.py"),
              "limits": manifest["limits"]}
    gen.write_json(output / "evaluation.json", report)
    print("Generated 290 candidates, including all 87 target-character templates; production/v1 unchanged", flush=True)
    print("fit", fit_results["counts_known_character_known_role"], flush=True)
    print("test", test_results["counts_known_character_known_role"], flush=True)


if __name__ == "__main__":
    main()
