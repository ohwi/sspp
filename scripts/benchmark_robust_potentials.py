"""Frozen card-only robustness matrix; genuine native role pools, supplied char.

Uses the independently implemented raw-sprite NumPy renderer in the frozen
synthetic benchmark, never the candidate generator. The committed baseline app
can be imported from an isolated scratch archive while current code is edited.
Expected PID, Corner and render bounds construct/score observations only.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
WORK = REPO.parent
ROOT = WORK / "robust-validation-v1"
BASELINE_REF = "70d75b76095642aa09ca5e59d0fd26453c149b85"
HELPER_SHA = "4510dc3a64e8afa579ea2eb6b8770682d6ebaa49f41020f5d29e57c2f4fc17d9"
MODEL_SHA = "3624d9cb8ff164f790ee89bdb0afdc6fcced5d22f3886c22ecd8f1883fceb12f"
SEED = 20261012


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_helpers():
    path = REPO / "scripts/benchmark_synthetic_potentials.py"
    if sha(path) != HELPER_SHA:
        raise ValueError("Frozen independent renderer changed")
    spec = importlib.util.spec_from_file_location("frozen_synthetic_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_scenarios(helper):
    base = [dict(s, cohort="all_roles", family="original9") for s in helper.SCENARIOS]
    windows = [{"id": f"window_{w}", "kind": "independent", "window_width": w,
                "cohort": "all_roles", "family": "window_width"}
               for w in [960, 1024, 1366, 1920, 2560]]
    affines = []
    for scale in [.90, .95, 1.05, 1.10]:
        for dx, dy in [(-4., 3.), (3., -2.)]:
            if scale == .95 and (dx, dy) == (3., -2.):
                continue  # Covered by original glyph_affine on all 1,280 cases.
            affines.append({"id": f"glyph_{scale:.2f}_shift_{dx:+.0f}_{dy:+.0f}",
                            "kind": "independent", "glyph_scale": scale,
                            "glyph_shift": [dx, dy], "cohort": "balanced800",
                            "family": "glyph_geometry"})
    return base, windows, affines


def choose_subset(cases):
    """10 per native context; fixed index grid, canonical alias membership kept."""
    contexts = defaultdict(list)
    for case in cases:
        contexts[(case["entry"]["character_key"], case["role"])].append(case)
    selected = []
    grid = [0, 1, 3, 5, 6, 8, 10, 11, 13, 15]
    for context, members in contexts.items():
        chosen = set(grid)
        mandatory = {i for i, case in enumerate(members) if len(case["aliases"]) > 1}
        for index in sorted(mandatory - chosen):
            removable = sorted(chosen - mandatory - {0, 1}, reverse=True)
            if not removable:
                raise ValueError("Cannot retain aliases and zero-target specific pair")
            chosen.remove(removable[0])
            chosen.add(index)
        assert len(chosen) == 10
        selected.extend(members[i] for i in sorted(chosen))
    if len(selected) != 800 or sum(len(c["aliases"]) > 1 for c in selected) != 10:
        raise ValueError("Unexpected balanced context/alias coverage")
    return selected


def select_pilot(work, count):
    """All scenario kinds appear: 100 yields four positives/three per negative."""
    buckets = defaultdict(list)
    for index, (_, scenario) in enumerate(work):
        buckets[scenario["id"]].append(index)
    if count < len(buckets):
        raise ValueError("Pilot must cover every frozen scenario")
    import numpy as np
    chosen = []
    quotient, remainder = divmod(count, len(buckets))
    for number, indexes in enumerate(buckets.values()):
        n = quotient + (number < remainder)
        chosen.extend(indexes[i] for i in np.linspace(0, len(indexes) - 1, min(n, len(indexes)), dtype=int))
    return [(i, *work[i]) for i in sorted(set(chosen))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=ROOT / "baseline-70d75b7/app")
    parser.add_argument("--assets-dir", type=Path, default=WORK / "synthetic-validation-v1/assets")
    parser.add_argument("--run-label", default="baseline")
    parser.add_argument("--source-label", default=BASELINE_REF)
    parser.add_argument("--pilot-count", type=int, default=0)
    parser.add_argument("--opencv-threads", type=int, default=None,
                        help="Process-only runtime setting; inference policy and weights stay fixed")
    args = parser.parse_args()
    if not args.run_label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in args.run_label):
        raise ValueError("Run label must be a simple directory name")
    helper = load_helpers()
    app = args.app_dir.resolve()
    if not (app / "src/worker.py").is_file():
        raise ValueError("Application directory is missing")
    # The helper itself imports no application modules. Give this isolated app
    # first priority before importing config/worker/catalog in a fresh process.
    sys.path = [p for p in sys.path if p != str(REPO / "app")]
    sys.path.insert(0, str(app))
    import cv2
    import numpy as np
    if args.opencv_threads is not None:
        if args.opencv_threads < 1:
            raise ValueError("OpenCV thread count must be positive")
        cv2.setNumThreads(args.opencv_threads)
    import config
    import src.worker as worker_module
    import src.load_image as images_module
    from src.character_catalog import load_catalog
    from src.load_build import BuildLoader
    from src.load_resolution import get_capture_area
    from src.sstoy_loader.formatter import save_game_preset_to_json
    if not Path(config.__file__).resolve().is_relative_to(app) or not Path(worker_module.__file__).resolve().is_relative_to(app):
        raise ValueError("Application imports escaped the chosen source snapshot")
    logging.disable(logging.CRITICAL)
    run = ROOT / args.run_label
    run.mkdir(parents=True, exist_ok=True)
    report_path, csv_path = run / "benchmark-report.json", run / "benchmark-results.csv"
    if report_path.exists() or csv_path.exists():
        raise ValueError("Refusing to replace a completed run")
    manifest_path = args.assets_dir / "potential-manifest.json"
    manifest = read(manifest_path)
    entries = manifest["entries"]
    skills = images_module._load_skill_images(args.assets_dir / "potentials")
    if len(entries) != 1160 or len(skills) != 40 or sum(map(len, skills.values())) != 1160:
        raise ValueError("Full 40-character/1160-PID candidate corpus is required")
    catalog = load_catalog()
    model_manifest = read(app / "resources/models/badge-model.json")
    if sha(app / "resources/models/weights.npz") != MODEL_SHA:
        raise ValueError("Frozen weights changed")
    positive, declared, cache, partition = helper.strata(entries, manifest["sources"], model_manifest)
    protected = [p for p in app.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    protected += list((REPO / "app/resources/templates").rglob("*"))
    protected += list((REPO / "app/resources/models").rglob("*"))
    protected += [REPO / "app/resources/characters.json", Path(__file__), REPO / "scripts/benchmark_synthetic_potentials.py", manifest_path]
    protected += list((args.assets_dir / "potentials").rglob("*.png"))
    protected += [Path(s["local_path"]) for s in manifest["sources"].values()]
    before = helper.file_snapshot(protected)
    by_pid = {str(e["potential_id"]): e for e in entries}
    cases, aliases, loaders, receipts = [], [], {}, []
    builds = run / "native-builds"
    builds.mkdir(exist_ok=True)
    for cid, character in sorted(catalog["characters"].items(), key=lambda pair: int(pair[0])):
        for role in ["main", "assist"]:
            code, targets = helper.encode_native(catalog, int(cid), role)
            ok, path = save_game_preset_to_json(code, str(builds), f"robust-{cid}-{role}")
            if not ok:
                raise ValueError(path)
            loader = BuildLoader(path)
            pids = [str(pid) for group, _ in helper.groups(character, role) for pid in group]
            if loader.target_level_map != targets or set(loader.get_recognition_potentials(character["key"])) != set(pids):
                raise ValueError("Genuine native import changed role/target values")
            zero = [p for p in pids if targets[character["key"]][p] == 0]
            if len(pids) != 16 or not zero:
                raise ValueError("Native context must preserve all 16 and zero targets")
            loaders[(character["key"], role)] = loader
            receipts.append({"character_id": int(cid), "key": character["key"], "role": role,
                             "code": code, "path": path, "sha256": sha(path), "role_count": 16,
                             "zero_pids": zero, "targets": targets[character["key"]]})
            visuals = defaultdict(list)
            for pid in pids:
                visuals[helper.pixel_key(skills[character["key"]][pid + ".png"]["bgr"])].append(pid)
            for key, members in visuals.items():
                if len(members) > 1:
                    aliases.append({"character_key": character["key"], "role": role, "pids": members, "canonical_pixels": key})
            for pid in pids:
                key = helper.pixel_key(skills[character["key"]][pid + ".png"]["bgr"])
                cases.append({"entry": by_pid[pid], "role": role, "aliases": visuals[key], "visual_key": key})
    if len(cases) != 1280 or len(aliases) != 5:
        raise ValueError("Role/alias corpus changed")
    subset = choose_subset(cases)
    original, windows, affines = make_scenarios(helper)
    plan = {"schema_version": 1, "seed": SEED, "base_scenarios": original,
            "extra_window_scenarios": windows, "extra_glyph_scenarios": affines,
            "window_widths_covered": [960, 1024, 1280, 1366, 1600, 1920, 2560],
            "native_role_cases": 1280, "subset_role_cases": 800,
            "subset_policy": "10 per char/native-role from fixed indices0,1,3,5,6,8,10,11,13,15; retain all active canonical aliases and first two specific slots",
            "subset_pids": [{"character_key": c["entry"]["character_key"], "role": c["role"], "potential_id": str(c["entry"]["potential_id"])} for c in subset],
            "independent_cases": 23520, "identity_controls": 1280, "safe_reject_cases": 320,
            "planned_total": 25120, "renderer_helper_sha256": HELPER_SHA,
            "input_policy": "Known char + genuine native16 only; GT PID/Corner/bbox never reaches inference",
            "negative_policy": "Three uniform blank frames + first specific card desaturated per char/role; no legal catalog-tuple swaps"}
    plan_bytes = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()
    plan_path = ROOT / "scenario-plan.json"
    if plan_path.exists() and plan_path.read_bytes() != plan_bytes:
        raise ValueError("Refusing to change the frozen matrix")
    if not plan_path.exists():
        plan_path.write_bytes(plan_bytes)
    work = [(c, {"id": "identity_copy", "kind": "identity_control", "family": "loading_control"}) for c in cases]
    work += [(c, s) for c in cases for s in original]
    work += [(c, s) for c in cases for s in windows]
    work += [(c, s) for c in subset for s in affines]
    representatives = {}
    for c in cases:
        representatives.setdefault((c["entry"]["character_key"], c["role"]), c)
    work += [(c, {"id": s, "kind": "safe_reject", "family": "safe_rejection"}) for c in representatives.values() for s in helper.NEGATIVES]
    if len(work) != 25120:
        raise ValueError("Unexpected frozen matrix size")
    selected = select_pilot(work, args.pilot_count) if args.pilot_count else [(i, *item) for i, item in enumerate(work)]
    renderer = helper.Renderer(manifest["sources"])
    worker = worker_module.MatcherWorker("")
    if worker.badge_model is None or worker.badge_model.model_sha256 != MODEL_SHA:
        raise ValueError("Actual worker did not load frozen model")
    signals, debug = [], []
    worker.match_signal.connect(lambda *v: signals.append(v))
    worker.debug_signal.connect(lambda *v: debug.append(v))
    observed_inputs = []
    original_match_card = worker_module.match_card
    def record_input(image, *positional, **keywords):
        # Observe the exact pixels handed from Worker to the matcher. Delegate
        # unchanged; this wrapper never sees a GT PID, Corner or render bounds.
        observed_inputs.append({"shape": list(image.shape),
                                "sha256": hashlib.sha256(image.tobytes()).hexdigest()})
        return original_match_card(image, *positional, **keywords)
    worker_module.match_card = record_input
    base_area = get_capture_area({"x": 0, "y": 0, "w": 1280, "h": 720}, config.ROIS[0])
    rows, failures = [], []
    start = time.monotonic()
    for order, (case_index, case, scenario) in enumerate(selected):
        entry, role = case["entry"], case["role"]
        pid, char = str(entry["potential_id"]), entry["character_key"]
        seed = int.from_bytes(hashlib.sha256(f"{SEED}:{case['visual_key']}:{scenario['id']}".encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        card = skills[char][pid + ".png"]["bgr"].copy() if scenario["kind"] == "identity_control" else renderer.render(entry, scenario)
        if scenario.get("ribbon"):
            cv2.rectangle(card, (95, 0), (149, 12), (120, 50, 225), -1)
            cv2.putText(card, "SELECT", (98, 9), cv2.FONT_HERSHEY_SIMPLEX, .22, (255, 255, 255), 1)
        if scenario.get("crop_edge"):
            card[-scenario["crop_edge"]:] = 0
            card[:, -scenario["crop_edge"]:] = 0
        if scenario.get("noise_std"):
            card = np.clip(np.rint(card.astype(float) + rng.normal(0, scenario["noise_std"], card.shape)), 0, 255).astype(np.uint8)
        if scenario.get("gamma"):
            card = np.clip(np.rint((card / 255.) ** scenario["gamma"] * 255. * scenario["brightness"] + scenario["offset"]), 0, 255).astype(np.uint8)
        if scenario.get("blur_sigma"):
            card = cv2.GaussianBlur(card, (3, 3), scenario["blur_sigma"])
        if scenario["id"] == "missing_color":
            card = cv2.cvtColor(cv2.cvtColor(card, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        observed_equivalent = helper.pixel_key(card) == case["visual_key"]
        frame = np.zeros((720, 1280, 3), np.uint8)
        height, width = card.shape[:2]
        left, top = base_area["left"] + (base_area["width"] - width) // 2, base_area["top"] + 35
        frame[top:top + height, left:left + width] = card
        if scenario["id"].startswith("blank_"):
            frame[:] = {"blank_black": 0, "blank_gray": 128, "blank_white": 255}[scenario["id"]]
        window = scenario.get("window_width", 1280)
        if window != 1280:
            frame = cv2.resize(frame, (window, round(720 * window / 1280)), interpolation=cv2.INTER_LINEAR)
        if scenario.get("jpeg_quality"):
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, scenario["jpeg_quality"]])
            if not ok:
                raise ValueError("JPEG encoding failed")
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        geometry = {"x": 0, "y": 0, "w": frame.shape[1], "h": frame.shape[0]}
        worker.build_loader = loaders[(char, role)]
        signals.clear()
        debug.clear()
        observed_inputs.clear()
        tick = time.monotonic()
        worker.detect_skill(helper.SavedCapture(cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)), geometry,
                            config.ROIS[0], char, skills, config.REFERENCE_WIDTH / geometry["w"], 0)
        elapsed_inference = time.monotonic() - tick
        if len(signals) != 1:
            raise ValueError("Unexpected decision count")
        _, text, score, matched, actual_priority, target = signals[0]
        predicted = text[:-4] if matched and text.endswith(".png") and text[:-4].isdigit() else None
        ambiguous = len(case["aliases"]) > 1
        if scenario["kind"] == "safe_reject":
            outcome = "safe_reject" if predicted is None else "unsafe_confirmation"
        elif ambiguous:
            outcome = "ambiguity_refused" if predicted is None else "ambiguity_confirmed"
        else:
            outcome = "unknown" if predicted is None else ("correct" if predicted == pid else "wrong")
        source = manifest["sources"]["sstoy:" + entry["meta"]["glyph"]]
        source_key = (entry["meta"]["glyph"], source["sha256"])
        loader = loaders[(char, role)]
        expected_target, expected_priority = loader.get_target_level(char, pid + ".png"), loader.get_priority(char, pid + ".png")
        row = {"execution_order": order, "matrix_case": case_index, "kind": scenario["kind"], "scenario": scenario["id"],
               "scenario_family": scenario["family"], "window_width": window, "window_height": frame.shape[0],
               "glyph_scale": scenario.get("glyph_scale", 1.), "glyph_shift_x": scenario.get("glyph_shift", [0., 0.])[0],
               "glyph_shift_y": scenario.get("glyph_shift", [0., 0.])[1], "character_id": entry["character_id"],
               "character_key": char, "role": role, "potential_role": entry["role"], "expected_pid": pid,
               "predicted_pid": predicted, "outcome": outcome, "expected_ambiguity": ambiguous,
               "alias_pids": "|".join(case["aliases"]), "score": score, "actual_priority": actual_priority,
               "expected_priority": expected_priority, "target_level": target, "expected_target_level": expected_target,
               "expected_zero_target": expected_target == 0,
               "target_preserved_if_correct": target == expected_target if predicted == pid else None,
               "priority_preserved_if_correct": actual_priority == expected_priority if predicted == pid else None,
               "positive_weight_source_seen": source_key in positive, "declared_receipt_source_seen": source_key in declared,
               "reference_cache_source_seen": source_key in cache, "source_path": source_key[0], "source_sha256": source_key[1],
               "card_pixels_equivalent_before_window_resampling": observed_equivalent,
               "inference_pixel_receipts": json.dumps(observed_inputs, separators=(",", ":")),
               "inference_seconds": elapsed_inference, "variation_seed": seed}
        rows.append(row)
        if outcome in ["unknown", "wrong", "ambiguity_confirmed", "unsafe_confirmation"]:
            failure_dir = run / "failures"
            failure_dir.mkdir(exist_ok=True)
            stem = f"{case_index:05d}-{char}-{role}-{pid}-{scenario['id']}"
            image_path, log_path = failure_dir / (stem + ".png"), failure_dir / (stem + ".json")
            patch = helper.SavedCapture(frame).grab(get_capture_area(geometry, config.ROIS[0]))
            cv2.imencode(".png", patch)[1].tofile(image_path)
            record = {"case": row, "signals": [list(v) for v in signals], "debug": [list(v) for v in debug],
                      "scenario_parameters": scenario, "source_refs": entry["source_refs"],
                      "captured_fixed_roi_image": str(image_path), "image_sha256": sha(image_path)}
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            failures.append({"matrix_case": case_index, "outcome": outcome, "image": str(image_path), "log": str(log_path)})
        if (order + 1) % 500 == 0:
            progress = {"run_label": args.run_label, "completed": order + 1, "total": len(selected),
                        "elapsed_seconds": time.monotonic() - start}
            (run / "progress.json").write_text(json.dumps(progress) + "\n", encoding="utf-8")
            print(json.dumps(progress), flush=True)
    elapsed = time.monotonic() - start
    after = helper.file_snapshot(protected)
    changed = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
    target_failures = [r["matrix_case"] for r in rows if r["outcome"] == "correct" and
                       not (r["target_preserved_if_correct"] and r["priority_preserved_if_correct"])]
    def summary(items):
        return {"cases": len(items), "outcomes": dict(Counter(r["outcome"] for r in items)),
                "resolvable_cases": sum(r["outcome"] in ["correct", "wrong", "unknown"] for r in items)}
    with csv_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "synthetic_card_only_known_character_genuine_native_role_not_face_or_live_app_accuracy",
              "app_dir": str(app), "source_label": args.source_label, "source_module_paths": {"worker": worker_module.__file__, "config": config.__file__},
              "run_label": args.run_label, "pilot": bool(args.pilot_count), "seed": SEED, "matrix_plan": plan,
              "scenario_plan_sha256": sha(plan_path), "executed_cases": len(rows), "planned_cases": 25120,
              "same_role_alias_groups": aliases, "native_import_receipts": receipts, "glyph_source_partitions": partition,
              "summary": {k: summary([r for r in rows if r["kind"] == k]) for k in ["identity_control", "independent", "safe_reject"]},
              "per_scenario": {s: summary([r for r in rows if r["scenario"] == s]) for s in sorted({r['scenario'] for r in rows})},
              "by_window_width": {str(w): summary([r for r in rows if r["kind"] == "independent" and r["window_width"] == w]) for w in [960, 1024, 1280, 1366, 1600, 1920, 2560]},
              "source_strata_independent": {str(v): summary([r for r in rows if r["kind"] == "independent" and r["positive_weight_source_seen"] == v]) for v in [True, False]},
              "target_or_priority_preservation_failures": target_failures,
              "correct_target_zero": sum(r["outcome"] == "correct" and r["expected_zero_target"] for r in rows),
              "elapsed_seconds": elapsed, "mean_inference_seconds": sum(r["inference_seconds"] for r in rows) / len(rows),
              "pilot_projected_full_seconds": elapsed / len(rows) * 25120 if args.pilot_count else None,
              "script_sha256": sha(Path(__file__)), "model_sha256": MODEL_SHA,
              "runtime": {"opencv_threads": cv2.getNumThreads(), "requested_opencv_threads": args.opencv_threads,
                          "thread_setting_changes_policy": False},
              "csv": {"path": str(csv_path), "sha256": sha(csv_path)}, "failure_cases": failures,
              "protected_sources_before": before, "protected_sources_after": after,
              "changed_protected_files": changed, "protected_sources_unchanged": not changed,
              "checks_passed": not changed and not target_failures,
              "limits": ["Known char is supplied; no face or end-to-end/live UI performance claim.",
                         "Raw public sprites and numeric geometry assumptions are shared; renderer implementation is independent.",
                         "Window-width sweep uses nominal glyph geometry; extra glyph-scale/translation conditions are at1280, not a full cross-product.",
                         "Identity controls, ordinary unknowns, canonical ambiguity refusals and safe rejects remain separate.",
                         "Missing-color samples are first specific card per80contexts; not every normal-card desaturation.",
                         "Frozen model; no training or threshold selection on these outcomes.",
                         "Repeated glyphs/scenarios are correlated observations; no independent-trial confidence claim."]}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "sha256": sha(report_path), "summary": report["summary"],
                      "elapsed_seconds": elapsed, "projected_full_seconds": report["pilot_projected_full_seconds"],
                      "checks_passed": report["checks_passed"]}, ensure_ascii=True), flush=True)
    if not report["checks_passed"]:
        raise RuntimeError("Protected sources or native outputs changed")


if __name__ == "__main__":
    main()
