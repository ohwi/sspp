"""Fit three 65px face references from the even-indexed saved screenshots.

No test image supplies pixels or selects a reference. No live capture or GUI.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "app"))
import cv2
import numpy as np
from config import FACE_OFFSET, REFERENCE_WIDTH, ROIS
from src.load_image import _load_face_icons, create_mask
from src.load_resolution import get_capture_area

logging.disable(logging.CRITICAL)
TARGETS = {"allie": 139, "donna": 130, "suntide_willow": 160}
SIZE = 65
MAX_DIFF = 0.15


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(roots, files=()):
    paths = [path for root in roots for path in root.rglob("*") if path.is_file()]
    paths += [path for path in files if path.is_file()]
    return {str(path.resolve()): sha(path) for path in sorted(set(paths))}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class OfflineCapture:
    def __init__(self, image):
        self.image = image

    def grab(self, area):
        x, y = area["left"], area["top"]
        w, h = area["width"], area["height"]
        if min(x, y) < 0 or x + w > self.image.shape[1] or y + h > self.image.shape[0]:
            raise ValueError("ROI outside screenshot")
        return self.image[y:y + h, x:x + w]


def normalized_roi(image, geo, roi):
    """The production worker's crop-then-resize-then-grayscale path."""
    area = get_capture_area(geo, roi, FACE_OFFSET)
    crop = OfflineCapture(image).grab(area)
    scale = REFERENCE_WIDTH / geo["w"]
    if scale != 1.0:
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
                          interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    return cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY), area


def face_diff(image, template, mask):
    """Equivalent to the worker's finite-filtered masked SQDIFF branch."""
    if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
        return None
    scores = cv2.matchTemplate(image, template, cv2.TM_SQDIFF_NORMED, mask=mask)
    scores = scores[np.isfinite(scores)]
    scores = scores[scores >= -1e-6]
    return max(0.0, float(scores.min())) if scores.size else None


def load_client(record):
    source = Path(record["frozen"])
    if sha(source) != record["sha256"] or sha(Path(record["source"])) != record["sha256"]:
        raise ValueError("Original/frozen source hash mismatch: " + record["id"])
    image = cv2.imdecode(np.frombuffer(source.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    h, w = image.shape[:2]
    if [w, h] != [record["width"], record["height"]]:
        raise ValueError("Source dimensions differ from receipt")
    crop = [1, 31, 1600, 900] if (w, h) == (1602, 932) else [0, 0, w, h]
    x, y, crop_w, crop_h = crop
    image = image[y:y + crop_h, x:x + crop_w]
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image, {"x": 0, "y": 0, "w": crop_w, "h": crop_h}, crop


def extract_fit_face(gray):
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=30,
                              param1=100, param2=20, minRadius=30, maxRadius=34)
    if circles is None:
        raise ValueError("No circular face frame found in fit ROI")
    possibilities = []
    for cx, cy, radius in circles[0]:
        x, y = math.floor(float(cx) - SIZE // 2), math.floor(float(cy) - SIZE // 2)
        if x >= 0 and y >= 0 and x + SIZE <= gray.shape[1] and y + SIZE <= gray.shape[0]:
            possibilities.append((abs(float(cx) - gray.shape[1] / 2), x, y, float(cx), float(cy), float(radius)))
    if not possibilities:
        raise ValueError("Fit circle does not contain a full 65px crop")
    _, x, y, cx, cy, radius = min(possibilities)
    return gray[y:y + SIZE, x:x + SIZE].copy(), [x, y, SIZE, SIZE], [cx, cy, radius]


def write_png(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("PNG encode failed")
    raw = encoded.tobytes()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Refusing to replace an existing face reference: " + str(path))
        return
    with path.open("xb") as stream:
        stream.write(raw)


def best_face(gray, templates):
    scores = [(diff, key) for key, (template, mask) in templates.items()
              if (diff := face_diff(gray, template, mask)) is not None]
    diff, key = min(scores) if scores else (1.0, None)
    return key, diff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "--output-dir", dest="output", type=Path, default=WORK_ROOT / "calibration-v2")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.output == REPO_ROOT or REPO_ROOT in args.output.parents:
        raise ValueError("Candidate output must stay outside the application repository")
    if (args.output / "faces-manifest.json").exists():
        raise ValueError("Face manifest already exists; select a new output folder")
    source_root = WORK_ROOT / "screenshot-validation"
    input_path = source_root / "inputs.json"
    label_paths = [source_root / "ground-truth-a.json", source_root / "ground-truth-b.json"]
    catalog_path = REPO_ROOT / "app" / "resources" / "characters.json"
    records = sorted(read_json(input_path)["files"], key=lambda row: (Path(row["source"]).stem, row["id"]))
    if len(records) != 16 or len({row["id"] for row in records}) != 16:
        raise ValueError("Exactly sixteen frozen screenshot IDs are required")
    labels = {}
    for path in label_paths:
        for row in read_json(path)["files"]:
            if row["id"] in labels:
                raise ValueError("Duplicate ground truth ID")
            labels[row["id"]] = sorted(row["cards"], key=lambda card: card["index"])
    catalog = read_json(catalog_path)
    for key, char_id in TARGETS.items():
        if catalog["characters"][str(char_id)]["key"] != key:
            raise ValueError("Character key and stable ID mismatch")
    split = [{"ordered_index": index, "id": row["id"], "partition": "fit" if index % 2 == 0 else "test",
              "sha256": row["sha256"], "source": row["source"], "frozen": row["frozen"]}
             for index, row in enumerate(records)]
    split_hash = hashlib.sha256(json.dumps(split, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()
    protected_roots = [REPO_ROOT / "app" / "resources" / "templates", WORK_ROOT / "generated-templates"]
    protected_files = [catalog_path, input_path, *label_paths,
                       *(Path(row[key]) for row in records for key in ("source", "frozen"))]
    before = snapshot(protected_roots, protected_files)
    mask = create_mask(SIZE, SIZE)
    references = []
    fit_rois = defaultdict(list)
    # No test source is decoded before references and primary choices are frozen.
    for index, record in enumerate(records):
        if index % 2:
            continue
        image, geo, crop = load_client(record)
        if sorted(card["index"] for card in labels[record["id"]]) != [0, 1, 2]:
            raise ValueError("Expected three labeled cards per screenshot")
        for card in labels[record["id"]]:
            char = card["character_key"]
            if char not in TARGETS or card.get("certainty") != "high":
                raise ValueError("Fit face requires one of the three high-certainty character labels")
            slot = card["index"]
            gray, area = normalized_roi(image, geo, ROIS[slot])
            patch, patch_crop, circle = extract_fit_face(gray)
            relative = f"face-references/{char}/{record['id']}-{slot}.png"
            path = args.output / relative
            write_png(path, patch)
            # ROI-local resize is rounded in both dimensions; record its exact mapping.
            sx, sy = gray.shape[1] / area["width"], gray.shape[0] / area["height"]
            original_equivalent = [crop[0] + area["left"] + patch_crop[0] / sx,
                                   crop[1] + area["top"] + patch_crop[1] / sy,
                                   patch_crop[2] / sx, patch_crop[3] / sy]
            reference = {"character_id": TARGETS[char], "character_key": char, "source_image_id": record["id"],
                         "source_ordered_index": index, "source_partition": "fit", "source_sha256": record["sha256"],
                         "source_file": record["frozen"], "card_index": slot, "client_crop_xywh": crop,
                         "face_roi_in_client_xywh": [area["left"], area["top"], area["width"], area["height"]],
                         "normalized_face_roi_dimensions": [gray.shape[1], gray.shape[0]],
                         "crop_in_normalized_face_roi_xywh": patch_crop, "hough_circle_cx_cy_radius": circle,
                         "equivalent_original_float_xywh": original_equivalent,
                         "relative_path": relative, "sha256": sha(path), "width": SIZE, "height": SIZE,
                         "channels": 1, "pixel_selection": "fit_only_normalized_face_roi_circle"}
            references.append((reference, patch))
            fit_rois[char].append((record["id"], slot, gray))
    primary = []
    calibrated = {}
    for char in sorted(TARGETS):
        candidates = [(row, patch) for row, patch in references if row["character_key"] == char]
        ranked = []
        for row, patch in candidates:
            differences = [face_diff(gray, patch, mask) for _, _, gray in fit_rois[char]]
            ranked.append((sum(differences) / len(differences), max(differences), row["source_image_id"], row["card_index"], row, patch))
        mean, maximum, _, _, selected, patch = min(ranked, key=lambda item: item[:4])
        relative = f"icons/{char}.png"
        write_png(args.output / relative, patch)
        calibrated[char] = (patch, mask)
        primary.append({"character_id": TARGETS[char], "character_key": char,
                        "name_ko": catalog["characters"][str(TARGETS[char])]["name_ko"],
                        "relative_path": relative, "sha256": sha(args.output / relative),
                        "width": SIZE, "height": SIZE, "channels": 1,
                        "selected_fit_reference": selected, "selection": "minimum_mean_masked_diff_on_same_character_fit_rois_only",
                        "fit_mean_diff": mean, "fit_max_diff": maximum,
                        "status": "candidate_fit_reference_not_registered"})
    frozen_primary_hashes = {row["relative_path"]: row["sha256"] for row in primary}
    baseline = _load_face_icons(REPO_ROOT / "app" / "resources" / "templates" / "icons")
    v1 = _load_face_icons(WORK_ROOT / "generated-templates" / "icons")
    all_faces = {**baseline, **v1, **calibrated}
    models = {"known_party_three": calibrated, "all_catalog_forty": all_faces}
    if len(all_faces) != 40:
        raise ValueError("Expected exactly forty face identities in global comparison")
    actual_worker = None
    actual_engine = "local_equivalent_masked_TM_SQDIFF_NORMED"
    worker_import_error = None
    try:
        from src.worker import MatcherWorker
        actual_worker = MatcherWorker("")
        actual_engine = "production_worker_detect_face"
    except ImportError as error:
        worker_import_error = f"{type(error).__name__}: {error}"
    validation = []
    for index, record in enumerate(records):
        image, geo, crop = load_client(record)
        capture = OfflineCapture(image)
        for card in labels[record["id"]]:
            slot = card["index"]
            gray, _ = normalized_roi(image, geo, ROIS[slot])
            expected = card["character_key"]
            row = {"id": record["id"], "ordered_index": index, "partition": "fit" if index % 2 == 0 else "test",
                   "card_index": slot, "expected_character_key": expected, "certainty": card.get("certainty"),
                   "normalized_roi_dimensions": [gray.shape[1], gray.shape[0]], "models": {}}
            for name, templates in models.items():
                detected, diff = best_face(gray, templates)
                own_diff = face_diff(gray, calibrated[expected][0], mask)
                accepted = detected is not None and diff <= MAX_DIFF
                data = {"detected_character_key": detected, "best_diff": diff, "accepted": accepted,
                        "correct_identity": detected == expected, "correct_and_accepted": accepted and detected == expected,
                        "expected_reference_diff": own_diff, "template_identity_count": len(templates)}
                if actual_worker is not None:
                    worker_key, worker_diff = actual_worker.detect_face(capture, geo, ROIS[slot], templates, REFERENCE_WIDTH / geo["w"])
                    data["actual_worker_detected_character_key"] = worker_key
                    data["actual_worker_best_diff"] = worker_diff
                    data["local_worker_agreement"] = worker_key == detected and abs(worker_diff - diff) < 1e-8
                    if not data["local_worker_agreement"]:
                        raise ValueError("Local masked face metric disagrees with production detect_face")
                row["models"][name] = data
            validation.append(row)
    final_primary_hashes = {row["relative_path"]: sha(args.output / row["relative_path"]) for row in primary}
    if frozen_primary_hashes != final_primary_hashes:
        raise RuntimeError("A template changed after held-out evaluation began")
    after = snapshot(protected_roots, protected_files)
    changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    summary = {}
    for partition in ("fit", "test"):
        summary[partition] = {}
        part_rows = [row for row in validation if row["partition"] == partition]
        for model in models:
            data = [row["models"][model] for row in part_rows]
            summary[partition][model] = {"cards": len(data), "correct_and_accepted": sum(row["correct_and_accepted"] for row in data),
                                        "wrong_and_accepted": sum(row["accepted"] and not row["correct_identity"] for row in data),
                                        "rejected": sum(not row["accepted"] for row in data),
                                        "largest_expected_reference_diff": max(row["expected_reference_diff"] for row in data),
                                        "by_character": {char: {"cards": sum(row["expected_character_key"] == char for row in part_rows),
                                            "correct_and_accepted": sum(row["expected_character_key"] == char and row["models"][model]["correct_and_accepted"] for row in part_rows)}
                                                         for char in TARGETS}}
    manifest = {"schema_version": 2, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "candidate_fit_only_heldout_face_evaluated_not_registered", "primary_count": len(primary),
                "script_sha256": sha(Path(__file__)), "config_sha256": sha(REPO_ROOT / "app" / "config.py"),
                "catalog_sha256": sha(catalog_path), "worker_sha256_at_evaluation": sha(REPO_ROOT / "app" / "src" / "worker.py"),
                "input_manifest": {"path": str(input_path), "sha256": sha(input_path)},
                "label_provenance": [{"path": str(path), "sha256": sha(path)} for path in label_paths],
                "split_rule": "source_filename_date_time_sorted_zero_based_even_fit_odd_test", "split_hash": split_hash,
                "split": split, "fit_screenshots": 8, "test_screenshots": 8,
                "test_pixels_used_for_references": False, "test_used_for_template_selection": False,
                "primary_hashes_frozen_before_test": frozen_primary_hashes, "primary_hashes_after_test": final_primary_hashes,
                "method": {"size": SIZE, "crop_then_resize_grayscale": True, "reference_width": REFERENCE_WIDTH,
                           "mask": {"center": [32, 32], "radius": 32}, "method": "TM_SQDIFF_NORMED", "max_diff": MAX_DIFF,
                           "fit_circle_locator": {"method": "HoughCircles", "dp": 1, "minDist": 30, "param1": 100, "param2": 20,
                                                  "minRadius": 30, "maxRadius": 34},
                           "extraction": "integer_floor(center_minus32)_65square_after_worker_ROI_normalization",
                           "reference_images_are_grayscale": True},
                "files": primary, "fit_references": [row for row, _ in references],
                "evaluation_engine": actual_engine, "worker_import_error": worker_import_error,
                "validation": validation, "summary": summary,
                "invariants": {"original_production_v1_catalog_unchanged": not changed,
                               "changed_files": changed, "protected_snapshot_before": before, "protected_snapshot_after": after},
                "limits": ["Held-out units are eight screenshots with correlated three-card faces, not twenty-four independent captures.",
                           "Only three characters and the observed skin/poses are evaluated; no other character or skin is certified.",
                           "No blank or unrelated-screen negative validation is performed here; independent QA owns that check.",
                           "Manual game-only crops in the first five screenshots retain the original supplied dimensions.",
                           "No production templates, candidates v1, screenshot sources, or app code were modified."]}
    output = args.output / "faces-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sha256": sha(output), "primary": [{key: row[key] for key in ("character_key", "relative_path", "sha256", "fit_mean_diff", "fit_max_diff")} for row in primary],
                      "selected_fit_sources": [{"character_key": row["character_key"], "image_id": row["selected_fit_reference"]["source_image_id"], "slot": row["selected_fit_reference"]["card_index"]} for row in primary],
                      "engine": actual_engine, "summary": summary, "protected_unchanged": not changed}, ensure_ascii=True, indent=2))
    if changed:
        raise RuntimeError("Protected input changed during calibration")


if __name__ == "__main__":
    main()
