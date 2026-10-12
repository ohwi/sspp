"""Audit calibrated matching against frozen independent labels, without live capture.

The chronological even/odd 8/8 split is fixed before asset generation. This
script does no fitting and never registers templates or replaces baseline data.
Run with the repository's existing Python environment. Assets and screenshots
live under ../calibration-v2 and ../screenshot-validation by default.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
WORK = REPO.parent
sys.path.insert(0, str(REPO / "app"))

NATIVE_CODE = "AAAAiwAAAKAAAACCMAbDZDDNlgAABs2AQDwA"
PID_FILENAME = re.compile(r"^(\d+)\.png$")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def fixed_split(records):
    """No random split, score-dependent selection, or outcome-based reordering."""
    ordered = sorted(records, key=lambda row: row["id"])
    if len(ordered) != 16 or len({row["id"] for row in ordered}) != 16:
        raise ValueError("Exactly 16 unique frozen screenshots are required")
    if any(not re.fullmatch(r"\d{6}", row["id"]) for row in ordered):
        raise ValueError("Screenshot IDs must be HHMMSS")
    return {row["id"]: "fit" if index % 2 == 0 else "test"
            for index, row in enumerate(ordered)}


def client_geometry(record):
    """Retain original screenshot pixels; MSS areas include the client origin."""
    width, height = record["width"], record["height"]
    if (width, height) == (1602, 932):
        return {"x": 1, "y": 31, "w": 1600, "h": 900}
    return {"x": 0, "y": 0, "w": width, "h": height}


def merge_skills(original, calibrated):
    merged = {key: dict(value) for key, value in original.items()}
    for key, values in calibrated.items():
        merged.setdefault(key, {}).update(values)
    return merged


def load_labels(paths, records, catalog):
    labels = {}
    characters = {row["key"]: row for row in catalog["characters"].values()}
    for path in paths:
        for row in read_json(path)["files"]:
            if row["id"] in labels or sorted(c["index"] for c in row["cards"]) != [0, 1, 2]:
                raise ValueError("Duplicate or incomplete independent labels")
            for card in row["cards"]:
                character = characters[card["character_key"]]
                pid = card["potential_id"]
                if (card.get("certainty") != "high" or pid not in character["potentials"]
                        or character["potential_names"][pid] != card["name_ko"]):
                    raise ValueError("A visual label does not match its catalog character/PID/name")
            labels[row["id"]] = sorted(row["cards"], key=lambda c: c["index"])
    if set(labels) != {row["id"] for row in records}:
        raise ValueError("Independent labels must cover all frozen inputs")
    return labels


def evaluate_decisions(decision, cards, face_stage=True):
    """A threshold pass or matched=True character (?) is never a correct PID."""
    evaluated = []
    for card in cards:
        index = card["index"]
        signals = [s for s in decision["match_signals"] if s["index"] == index]
        if len(signals) != 1:
            raise ValueError("Worker must emit exactly one result per card")
        signal = signals[0]
        match = PID_FILENAME.fullmatch(signal["text"])
        pid = match.group(1) if signal["matched"] and match else None
        skill_status = "unknown" if pid is None else (
            "correct" if pid == card["potential_id"] else "wrong")
        predicted_character = None
        face_status = None
        if face_stage:
            faces = [s for s in decision["debug_signals"] if s["index"] == index
                     and s["text"].startswith("[FACE]")]
            # The emitted decision, rather than a guessed debug-score threshold,
            # determines whether the worker accepted the face stage.
            if signal["matched"] and len(faces) == 1:
                predicted_character = faces[0]["text"][6:]
            face_status = "unknown" if not predicted_character else (
                "correct" if predicted_character == card["character_key"] else "wrong")
        evaluated.append({
            "index": index, "expected_character_key": card["character_key"],
            "expected_potential_id": card["potential_id"], "name_ko": card["name_ko"],
            "predicted_character_key": predicted_character, "face_status": face_status,
            "predicted_potential_id": pid, "potential_status": skill_status,
            "score": signal["score"], "priority": signal["priority"],
            "target_level": signal["target_level"],
        })
    return evaluated


def summarize(rows):
    counts = Counter()
    per_character = defaultdict(Counter)
    for card in rows:
        counts["cards"] += 1
        counts["potential_" + card["potential_status"]] += 1
        per_character[card["expected_character_key"]]["cards"] += 1
        per_character[card["expected_character_key"]]["potential_" + card["potential_status"]] += 1
        if card["face_status"] is not None:
            counts["face_evaluated"] += 1
            counts["face_" + card["face_status"]] += 1
            per_character[card["expected_character_key"]]["face_" + card["face_status"]] += 1
    result = {key: counts[key] for key in ["cards", "potential_correct", "potential_wrong", "potential_unknown"]}
    result.update({"face_" + name: counts["face_" + name] if counts["face_evaluated"] else None
                   for name in ["correct", "wrong", "unknown"]})
    result["per_character"] = {key: dict(value) for key, value in sorted(per_character.items())}
    result["potential_correct_rate"] = counts["potential_correct"] / counts["cards"] if counts["cards"] else None
    accepted = counts["potential_correct"] + counts["potential_wrong"]
    result["potential_confirmed_precision"] = counts["potential_correct"] / accepted if accepted else None
    return result


def snapshot(paths):
    return {str(path.resolve()): sha256(path) for path in sorted(set(paths)) if path.is_file()}


def prepare_native_build(directory):
    """Use the public importer and genuine BuildLoader; compare an external oracle."""
    from src.character_catalog import load_catalog
    from src.load_build import BuildLoader
    from src.sstoy_loader.formatter import save_game_preset_to_json
    fixture_path = REPO / "tests/fixtures/ingame_presets/cases.json"
    fixture = next(row for row in read_json(fixture_path)["cases"] if row["code"] == NATIVE_CODE)
    ok, result = save_game_preset_to_json(NATIVE_CODE, str(directory), "offline-calibration-native-fixture")
    if not ok:
        raise ValueError("Native importer failed: " + result)
    loader = BuildLoader(result)
    catalog = load_catalog()
    expected_levels, expected_slots = {}, []
    for slot in fixture["expected_slots"]:
        key = catalog["characters"][str(slot["char_id"])]["key"]
        levels = slot["target_levels"]
        expected_levels[key] = levels
        expected_slots.append({"position": slot["position"], "character_id": slot["char_id"], "key": key})
        if len(levels) != 16 or not any(level == 0 for level in levels.values()):
            raise ValueError("Oracle must retain all 16 targets, including zero")
        for pid, level in levels.items():
            if loader.get_target_level(key, pid + ".png") != level:
                raise ValueError("BuildLoader changed an exact native target")
            if loader.get_priority(key, pid + ".png") != (2 if level > 0 else 0):
                raise ValueError("Compatibility priority disagrees with the exact target")
        if set(map(str, loader.get_recognition_potentials(key))) != set(levels):
            raise ValueError("Native recognition role filter omitted targets (including zero)")
    if loader.target_level_map != expected_levels or loader.build_data["slots"] != expected_slots:
        raise ValueError("Native slot order or exact target map differs from the oracle")
    if set(loader.get_recognition_characters()) != set(expected_levels):
        raise ValueError("Native character filter differs from its three slots")
    return Path(result), loader, {
        "code": NATIVE_CODE, "oracle_fixture": str(fixture_path), "oracle_sha256": sha256(fixture_path),
        "slots": expected_slots, "exact_target_count": sum(map(len, expected_levels.values())),
        "zero_target_count": sum(level == 0 for levels in expected_levels.values() for level in levels.values()),
        "all_roles_and_exact_targets_verified": True, "target_levels": expected_levels,
    }


def runtime_api(helper_path):
    spec = importlib.util.spec_from_file_location("sspp_frozen_capture_helper", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    from config import REFERENCE_WIDTH, ROIS
    from src.card_matcher import match_card
    from src.load_resolution import get_capture_area
    from src.worker import MatcherWorker, _capture_image
    return helper.OfflineCapture, helper.connect_results, MatcherWorker, _capture_image, match_card, get_capture_area, REFERENCE_WIDTH, ROIS


def run_decision(image, geometry, faces, skills, build_path, api, labels=None):
    OfflineCapture, connect_results, Worker, capture_image, match_card, capture_area, reference_width, rois = api
    worker = Worker(str(build_path))
    emitted, debug = connect_results(worker)
    worker.update_build(str(build_path))
    if worker.build_loader is None:
        raise ValueError(worker.build_error or "Actual worker did not load the native build")
    capture = OfflineCapture(image)
    if labels is None:
        worker.process_rois(capture, geometry, faces, skills)
    else:
        for card in labels:
            index = card["index"]
            worker.detect_skill(capture, geometry, rois[index], card["character_key"], skills,
                                reference_width / geometry["w"], index)
    if len(emitted) != 3:
        raise ValueError("Worker did not emit all three ROI decisions")
    # Direct diagnostics use the same capture helper and the worker's chosen face.
    # They supplement the emitted decisions; they never replace their correctness.
    diagnostics = []
    for signal in emitted:
        index = signal["index"]
        if labels is not None:
            key = labels[index]["character_key"]
        else:
            found = [s for s in debug if s["index"] == index and s["text"].startswith("[FACE]")]
            key = found[0]["text"][6:] if found and signal["matched"] else None
        if key not in skills:
            continue
        patch = capture_image(capture, capture_area(geometry, rois[index]), reference_width / geometry["w"])
        allowed = worker.build_loader.get_recognition_potentials(key)
        filename, score, detail = match_card(patch, key, skills, allowed_ids=allowed,
                                           badge_model=getattr(worker, "badge_model", None))
        diagnostics.append({"index": index, "character_key": key, "allowed_ids": sorted(map(str, allowed)) if allowed is not None else None,
                            "filename": filename, "score": score, "diagnostics": detail})
        parsed = PID_FILENAME.fullmatch(signal["text"])
        if parsed and filename != signal["text"]:
            raise ValueError("Direct calibrated matcher diagnostic disagrees with worker filename")
    return {"match_signals": emitted, "debug_signals": debug,
            "capture_requests": capture.requests, "card_diagnostics": diagnostics,
            "face_stage_evaluated": labels is None, "expected_pid_used_for_matching": False}


def negative_checks(image, geometry, faces, skills, native_path, legacy_path, api):
    import cv2
    import numpy as np
    OfflineCapture, _, Worker, capture_image, match_card, capture_area, reference_width, rois = api
    badge_model = getattr(Worker(str(native_path)), "badge_model", None)
    blanks = []
    for intensity in [0, 128, 255]:
        blank = np.full(image.shape, intensity, np.uint8)
        if blank.shape[2] == 4:
            blank[:, :, 3] = 255
        decision = run_decision(blank, geometry, faces, skills, native_path, api)
        ok = all(not s["matched"] and not s["text"] and s["target_level"] is None for s in decision["match_signals"])
        blanks.append({"intensity": intensity, "passed": ok, "decisions": decision})
    patch = capture_image(OfflineCapture(image), capture_area(geometry, rois[0]), reference_width / geometry["w"])
    key, pair = "allie", ("513913.png", "513933.png")
    from src.load_build import BuildLoader
    native = BuildLoader(native_path)
    legacy = BuildLoader(legacy_path)
    if legacy.get_recognition_potentials(key) is not None:
        raise ValueError("Legacy build unexpectedly supplies a role")
    templates = {key: {filename: skills[key][filename] for filename in pair}}
    pixels = [value["gray"] if isinstance(value, dict) else value for value in templates[key].values()]
    identical = np.array_equal(*pixels)
    unknown_file, unknown_score, unknown_detail = match_card(patch, key, templates, allowed_ids=None,
                                                         badge_model=badge_model)
    allowed = native.get_recognition_potentials(key)
    known_file, known_score, known_detail = match_card(patch, key, templates, allowed_ids=allowed,
                                                     badge_model=badge_model)
    duplicate = {"identical_candidate_gray_pixels": identical,
                 "no_role": {"filename": unknown_file, "score": unknown_score, "diagnostics": unknown_detail,
                             "passed": identical and unknown_file is None},
                 "native_role": {"filename": known_file, "score": known_score, "diagnostics": known_detail,
                                 "passed": known_file == "513913.png"}}
    return {"blank_frames": blanks, "duplicate_ambiguity": duplicate,
            "passed": all(row["passed"] for row in blanks) and duplicate["no_role"]["passed"] and duplicate["native_role"]["passed"]}


def willow_specific_regression(image_path, reference_path, faces, skills, build_path, api):
    """The previously saved scene is a separate regression, never an extra test fold."""
    import cv2
    import numpy as np
    from src.character_catalog import load_catalog
    reference = read_json(reference_path)
    filenames = reference["expected_same_scene"]
    if filenames != ["516023.png", "516022.png", "516021.png"]:
        raise ValueError("Previously validated Willow scene reference changed")
    image = cv2.imdecode(np.frombuffer(image_path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.shape[:2] != (900, 1600):
        raise ValueError("Previously saved Willow scene has unexpected dimensions")
    if image.ndim == 3 and image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    character = load_catalog()["characters"]["160"]
    labels = [{"index": index, "character_key": "suntide_willow", "potential_id": filename[:-4],
               "name_ko": character["potential_names"][filename[:-4]]}
              for index, filename in enumerate(filenames)]
    geometry = {"x": 0, "y": 0, "w": 1600, "h": 900}
    decision = run_decision(image, geometry, faces, skills, build_path, api)
    evaluated = evaluate_decisions(decision, labels)
    return {"scope": "separate_previously_saved_three_specific_card_regression",
            "image": str(image_path), "image_sha256": sha256(image_path),
            "reference": str(reference_path), "reference_sha256": sha256(reference_path),
            "excluded_from_fit_and_test_metrics": True, "geometry": geometry,
            "evaluated": evaluated, "decisions": decision,
            "passed": all(row["face_status"] == "correct" and row["potential_status"] == "correct" for row in evaluated)}


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=WORK / "screenshot-validation")
    parser.add_argument("--assets-dir", type=Path, default=WORK / "calibration-v2")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = args.output or args.assets_dir / "benchmark-results.json"
    if REPO in output.resolve().parents:
        raise ValueError("Benchmark output must be scratch data outside the repository")
    if output.exists():
        raise ValueError("Refusing to replace an existing benchmark result; use a fresh output name")
    import cv2
    import numpy as np
    from src.character_catalog import load_catalog
    from src.load_image import _load_face_icons, _load_skill_images
    logging.disable(logging.CRITICAL)
    inputs = args.data_dir / "inputs.json"
    label_paths = [args.data_dir / "ground-truth-a.json", args.data_dir / "ground-truth-b.json"]
    baseline_path = args.data_dir / "matching-raw.json"
    willow_image = WORK / "client-current.png"
    willow_reference = WORK / "live-validation.json"
    records = sorted(read_json(inputs)["files"], key=lambda r: r["id"])
    split = fixed_split(records)
    labels = load_labels(label_paths, records, load_catalog())
    api = runtime_api(args.data_dir / "run_offline_matching.py")
    baseline = read_json(baseline_path)
    if {r["id"] for r in baseline["results"]} != set(split):
        raise ValueError("Baseline and current frozen sample IDs differ")
    label_hashes = {sha256(path) for path in label_paths}
    if {row["sha256"] for row in baseline["diagnostic_label_provenance"]} != label_hashes:
        raise ValueError("Independent labels differ from the preserved baseline labels")
    expected_image_hashes = {row["id"]: row["sha256"] for row in records}
    if any(row["source_sha256"] != expected_image_hashes[row["id"]] for row in baseline["results"]):
        raise ValueError("Preserved baseline was measured against different screenshot bytes")
    production = REPO / "app/resources/templates"
    asset_paths = list((args.assets_dir / "icons").rglob("*.png")) + list((args.assets_dir / "potentials").rglob("*.png"))
    asset_paths += list(args.assets_dir.glob("*manifest*.json"))
    protected = list(production.rglob("*")) + asset_paths + [inputs, baseline_path, *label_paths,
        REPO / "app/resources/characters.json", REPO / "app/src/worker.py", REPO / "app/src/load_image.py", REPO / "app/src/card_matcher.py",
        REPO / "app/config.py", REPO / "app/src/load_build.py", REPO / "app/src/character_catalog.py",
        REPO / "app/src/ingame_preset.py", REPO / "app/src/sstoy_loader/formatter.py",
        REPO / "tests/fixtures/ingame_presets/cases.json",
        args.data_dir / "accuracy-audit.json", args.data_dir / "run_offline_matching.py"]
    protected += [Path(r["frozen"]) for r in records] + [willow_image, willow_reference]
    before = snapshot(protected)
    original_faces = _load_face_icons(production / "icons")
    original_skills = _load_skill_images(production / "potentials")
    calibrated_faces = _load_face_icons(args.assets_dir / "icons")
    all_calibrated_skills = _load_skill_images(args.assets_dir / "potentials")
    expected_characters = {"allie", "donna", "suntide_willow"}
    if set(calibrated_faces) != expected_characters or not expected_characters <= set(all_calibrated_skills):
        raise ValueError("Calibrated assets must contain the three separately evaluated characters")
    calibrated_skills = {key: all_calibrated_skills[key] for key in sorted(expected_characters)}
    if any(len(values) != 29 for values in calibrated_skills.values()):
        raise ValueError("Each evaluated character requires all 29 potential images")
    if any(not isinstance(value, dict) or not all(k in value for k in ["gray", "bgr", "meta"])
           for skills in calibrated_skills.values() for value in skills.values()):
        raise ValueError("Calibrated loader did not provide verified gray/BGR/metadata entries")
    union_faces = {**original_faces, **calibrated_faces}
    union_skills = merge_skills(original_skills, calibrated_skills)
    output.parent.mkdir(parents=True, exist_ok=True)
    evaluated, supplementary, raw_decisions = [], [], []
    baseline_evaluated = []
    with tempfile.TemporaryDirectory(prefix="benchmark-fixture-", dir=output.parent) as temporary:
        native_path, native_loader, build_receipt = prepare_native_build(Path(temporary))
        legacy_path = Path(temporary) / "role-unknown.json"
        legacy_path.write_text('{"characters": {}}\n', encoding="utf-8")
        models = {
            "calibrated_only_native": (calibrated_faces, calibrated_skills, native_path, False),
            "installed_union_native": (union_faces, union_skills, native_path, False),
            "installed_union_role_unknown": (union_faces, union_skills, legacy_path, False),
            "known_character_native_diagnostic": (union_faces, union_skills, native_path, True),
        }
        duplicate_image = None
        for chronological_index, record in enumerate(records):
            path = Path(record["frozen"])
            if sha256(path) != record["sha256"]:
                raise ValueError("Frozen screenshot hash changed: " + record["id"])
            image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
            if image is None or image.shape[:2] != (record["height"], record["width"]):
                raise ValueError("Frozen screenshot cannot be decoded at expected dimensions")
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
            geometry = client_geometry(record)
            if record["id"] == "050630":
                duplicate_image = (image, geometry)
            for name, (faces, skills, build_path, injected) in models.items():
                decisions = run_decision(image, geometry, faces, skills, build_path, api,
                                         labels[record["id"]] if injected else None)
                result = evaluate_decisions(decisions, labels[record["id"]], face_stage=not injected)
                for card in result:
                    card.update(image_id=record["id"], model=name, split=split[record["id"]])
                    if card["predicted_potential_id"] is not None and build_path == native_path:
                        expected = native_loader.get_target_level(card["predicted_character_key"] or card["expected_character_key"], card["predicted_potential_id"] + ".png")
                        if card["target_level"] != expected:
                            raise ValueError("Worker signal lost the actual BuildLoader target level")
                evaluated.extend(result)
                raw_decisions.append({"image_id": record["id"], "model": name, "split": split[record["id"]], "geometry": geometry, "decisions": decisions})
                if chronological_index < 5 and name == "installed_union_native":
                    approx = {"x": 0, "y": 0, "w": 1600, "h": 900}
                    alternate = run_decision(image, approx, faces, skills, build_path, api)
                    supplementary.append({"image_id": record["id"], "split": split[record["id"]],
                                          "geometry": approx, "excluded_from_primary_metrics": True,
                                          "evaluated": evaluate_decisions(alternate, labels[record["id"]]), "decisions": alternate})
            print(json.dumps({"image_id": record["id"], "split": split[record["id"]], "completed": True}), flush=True)
        if duplicate_image is None:
            raise ValueError("Expected independently labeled duplicate-pair sample is absent")
        negatives = negative_checks(*duplicate_image, union_faces, union_skills, native_path, legacy_path, api)
        willow_regression = willow_specific_regression(willow_image, willow_reference, union_faces, union_skills, native_path, api)
    for record in baseline["results"]:
        for name in ["production", "virtual_union", "virtual_union_advance_L", "virtual_union_default_SS", "injected_character_skill_diagnostic"]:
            result = evaluate_decisions(record[name], labels[record["id"]], face_stage=name != "injected_character_skill_diagnostic")
            for card in result:
                card.update(image_id=record["id"], model=name, split=split[record["id"]])
            baseline_evaluated.extend(result)
    summaries = {name: {fold: summarize([row for row in evaluated if row["model"] == name and (fold == "all" or row["split"] == fold)])
                       for fold in ["fit", "test", "all"]} for name in models}
    baseline_summary = {name: {fold: summarize([row for row in baseline_evaluated if row["model"] == name and (fold == "all" or row["split"] == fold)])
                              for fold in ["fit", "test", "all"]} for name in sorted({row["model"] for row in baseline_evaluated})}
    after = snapshot(protected)
    changed = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
    reasons = defaultdict(Counter)
    for row in raw_decisions:
        for diagnostic in row["decisions"]["card_diagnostics"]:
            reasons[row["model"]][str(diagnostic["diagnostics"].get("reason", "unspecified"))] += 1
    report = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "frozen_saved_screenshots_actual_worker_and_native_BuildLoader_no_live_capture",
        "split_policy": "chronological zero-based even indexes fit; odd indexes test; never adjusted using scores",
        "split": split, "fit_ids": [k for k, v in split.items() if v == "fit"], "test_ids": [k for k, v in split.items() if v == "test"],
        "geometry_policy": "first five actual full-image dimensions; remaining original BGRA with client origin (1,31) and 1600x900; supplementary approximate bounds excluded",
        "benchmark_performs_fitting": False, "native_build_validation": build_receipt,
        "library_counts": {"production_faces": len(original_faces), "production_potentials": sum(map(len, original_skills.values())),
                           "calibrated_faces": len(calibrated_faces), "calibrated_potentials": sum(map(len, calibrated_skills.values())),
                           "unassessed_calibrated_potentials_excluded": sum(len(values) for key, values in all_calibrated_skills.items() if key not in expected_characters),
                           "installed_union_faces": len(union_faces), "installed_union_potentials": sum(map(len, union_skills.values()))},
        "summaries": summaries, "immutable_baseline_summaries": baseline_summary,
        "evaluated_cards": evaluated, "raw_worker_decisions": raw_decisions,
        "matcher_reason_counts": {k: dict(v) for k, v in reasons.items()},
        "supplementary_first_five_approximate_geometry": supplementary, "negative_checks": negatives,
        "willow_specific_regression": willow_regression,
        "source_hashes_before": before, "source_hashes_after": after,
        "protected_inputs_unchanged": not changed, "changed_protected_inputs": changed,
        "asset_manifest_provenance": {str(p): read_json(p) for p in args.assets_dir.glob("*manifest*.json")},
        "script_sha256": sha256(Path(__file__)),
        "checks_passed": not changed and negatives["passed"] and willow_regression["passed"],
        "limitations": ["Only three characters in 16 related saved screenshots; repeated cards are not independent population trials.",
                        "Fit scores are not held-out validation. Test results must be reported separately.",
                        "After test aggregates have been inspected, later runs on this same fixed test fold are repeated regression checks, not fresh unseen validation.",
                        "Chronological interleaving leaves correlated screenshots across fit/test; this is a bounded regression evaluation.",
                        "The first five client bounds are approximate; supplementary geometry results do not replace primary metrics.",
                        "Known-character diagnostic supplies character labels and skips face detection; it is not end-to-end accuracy.",
                        "Native preset role constraints and zero target levels are included; this does not establish all 40-character or live-client quality.",
                        "Blank-frame and exact-duplicate checks cover these specified negatives only.",
                        "No thresholds or transforms are tuned by this benchmark; it verifies the frozen supplied assets and runtime."]
    }
    output.write_text(json.dumps(json_safe(report), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sha256": sha256(output), "summaries": summaries,
                      "checks_passed": report["checks_passed"]}, ensure_ascii=True), flush=True)
    if not report["checks_passed"]:
        raise RuntimeError("Negative or prior-scene regression checks failed, or protected inputs changed; see benchmark report")
    return report


if __name__ == "__main__":
    main()
