"""Frozen-model synthetic card diagnostics; supplied character, no face claim.

Input sprites are rendered with an independent NumPy inverse-bilinear,
premultiplied-alpha implementation. Generator/calibration renderers are never
imported. Exact asset copies are separate loading controls. Native codes are
independently encoded and imported through the actual formatter/BuildLoader.
"""
import argparse
import base64
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import struct
import sys
import time

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
WORK = REPO.parent
sys.path.insert(0, str(REPO / "app"))
SEED = 20261012
SCENARIOS = [
    {"id": "raw_nominal", "kind": "independent"},
    {"id": "glyph_affine", "kind": "independent", "glyph_scale": .95, "glyph_shift": [3., -2.]},
    {"id": "background_affine", "kind": "independent", "bg_scale": [1.03, .97], "bg_shift": [2., -2.]},
    {"id": "window_1600", "kind": "independent", "window_width": 1600},
    {"id": "gaussian_blur", "kind": "independent", "blur_sigma": .60},
    {"id": "light_gamma", "kind": "independent", "gamma": 1.10, "brightness": .94, "offset": 4.},
    {"id": "jpeg_q82", "kind": "independent", "jpeg_quality": 82},
    {"id": "selected_ribbon", "kind": "independent", "ribbon": True},
    {"id": "edge_crop_noise", "kind": "independent", "crop_edge": 2, "noise_std": 1.2},
]
NEGATIVES = ["blank_black", "blank_gray", "blank_white", "missing_color"]
MODEL_SHA = "3624d9cb8ff164f790ee89bdb0afdc6fcced5d22f3886c22ecd8f1883fceb12f"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_snapshot(paths):
    return {str(p.resolve()): sha(p) for p in sorted(set(paths)) if p.is_file()}


def pixel_key(image):
    return str(image.shape) + ":" + hashlib.sha256(image.tobytes()).hexdigest()


def groups(character, role):
    schema = character["preset_schema"]
    prefix = "Master" if role == "main" else "Assist"
    return [(schema[prefix + "SpecificPotentialIds"], 1),
            (schema[prefix + "NormalPotentialIds"], 3), (schema["CommonPotentialIds"], 3)]


def encode_native(catalog, character_id, role):
    """96-bit IDs then 120 MSB-first target bits; normal/common cycle 0..6."""
    other = [int(value) for value in sorted(catalog["characters"], key=int) if int(value) != character_id][:2]
    ids = [character_id, *other] if role == "main" else [other[0], character_id, other[1]]
    targets, pieces = {}, []
    for slot, cid in enumerate(ids):
        character = catalog["characters"][str(cid)]
        slot_role = "main" if slot == 0 else "assist"
        expected, normal_index = {}, 0
        for pids, width in groups(character, slot_role):
            for index, pid in enumerate(pids):
                level = (cid + slot + index) % 2 if width == 1 else (cid + slot + normal_index) % 7
                if width == 3:
                    normal_index += 1
                expected[str(pid)] = level
                pieces.append(format(level, f"0{width}b"))
        targets[character["key"]] = expected
    bits = "".join(pieces)
    if len(bits) != 120:
        raise ValueError("Native body must contain three 40-bit slots")
    code = base64.b64encode(struct.pack(">III", *ids) + int(bits, 2).to_bytes(15, "big")).decode("ascii")
    return code, targets


class SavedCapture:
    def __init__(self, frame):
        self.frame = frame

    def grab(self, area):
        x, y, w, h = area["left"], area["top"], area["width"], area["height"]
        if min(x, y) < 0 or x + w > self.frame.shape[1] or y + h > self.frame.shape[0]:
            raise ValueError("Fixed-window capture exceeds saved pixels")
        return self.frame[y:y + h, x:x + w]


class Renderer:
    """Independent float64 reverse mapping; final rounding only after all layers."""
    def __init__(self, sources):
        import cv2
        import numpy as np
        self.cv2, self.np, self.sources = cv2, np, sources
        self.images, self.grids = {}, {}

    def source(self, path):
        np, cv2 = self.np, self.cv2
        source_id = "sstoy:" + path
        receipt = self.sources[source_id]
        local = Path(receipt["local_path"])
        if path not in self.images:
            raw = local.read_bytes()
            if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
                raise ValueError("Raw sprite receipt mismatch: " + path)
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if blob != receipt["git_blob_sha"]:
                raise ValueError("Raw sprite Git blob mismatch: " + path)
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise ValueError("Raw sprite decode failed")
            if image.shape[2] == 3:
                image = np.dstack([image, np.full(image.shape[:2], 255, np.uint8)])
            self.images[path] = image
        return self.images[path]

    def sample(self, image, height, width, sx, sy, tx, ty, replicate=False):
        np = self.np
        grid_key = (height, width)
        if grid_key not in self.grids:
            self.grids[grid_key] = np.meshgrid(np.arange(width, dtype=np.float64), np.arange(height, dtype=np.float64))
        dx, dy = self.grids[grid_key]
        xs, ys = (dx - tx) / sx, (dy - ty) / sy
        x0, y0 = np.floor(xs).astype(np.int32), np.floor(ys).astype(np.int32)
        fx, fy = xs - x0, ys - y0
        result = np.zeros((height, width, image.shape[2]), np.float64)
        for xo, yo, weights in [(0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                                (0, 1, (1 - fx) * fy), (1, 1, fx * fy)]:
            x, y = x0 + xo, y0 + yo
            valid = (x >= 0) & (y >= 0) & (x < image.shape[1]) & (y < image.shape[0])
            value = image[np.clip(y, 0, image.shape[0] - 1), np.clip(x, 0, image.shape[1] - 1)]
            result += value * (weights if replicate else weights * valid)[:, :, None]
        return result

    def overlay(self, canvas, path, scale, center, tint=None):
        np = self.np
        raw = self.source(path).astype(np.float64)
        alpha = raw[:, :, 3:4] / 255.
        color = raw[:, :, :3] * (np.asarray(tint)[None, None, :] if tint is not None else 1.)
        premultiplied = np.concatenate([color * alpha, alpha], axis=2)
        sampled = self.sample(premultiplied, *canvas.shape[:2], scale, scale,
                              center[0] - raw.shape[1] * scale / 2., center[1] - raw.shape[0] * scale / 2.)
        return sampled[:, :, :3] + canvas * (1. - sampled[:, :, 3:4])

    def render(self, entry, scenario):
        np = self.np
        meta = entry["meta"]
        height, width = (150 if meta["stype"] == 42 else 170), 150
        variant = 6 if meta["stype"] == 42 else (4 if meta["rarity"] == 2 else 5)
        layout = meta["layout"]
        bg = layout["backgrounds"][str(variant)]
        raw_bg = self.source(f"public/assets/skill_icons/rare_vestige_card_s_{variant}.png")[:, :, :3].astype(np.float64)
        scale_variation = scenario.get("bg_scale", [1., 1.])
        shift = scenario.get("bg_shift", [0., 0.])
        sx, sy = bg["scale_x"] * scale_variation[0], bg["scale_y"] * scale_variation[1]
        canvas = self.sample(raw_bg, height, width, sx, sy,
                             bg["center_x"] + shift[0] - raw_bg.shape[1] * sx / 2., bg["top_y"] + shift[1], True)
        glyph = layout["glyph"]
        center = [glyph["center"][i] + scenario.get("glyph_shift", [0., 0.])[i] for i in range(2)]
        canvas = self.overlay(canvas, meta["glyph"], glyph["scale"] * scenario.get("glyph_scale", 1.), center)
        if meta["corner"]:
            shape = {1: "Diamond", 2: "Triangle", 3: "Round"}[meta["corner"]]
            badge = layout["corner"]
            # Tint is BGR; only the B layer is multiplied. A is the white outline.
            tint = np.array([227., 119., 155.]) / 255. if meta["rarity"] == 1 else np.array([4., 129., 219.]) / 255.
            canvas = self.overlay(canvas, f"public/assets/skill_icons/Potential_{shape}_B.png", badge["scale"], badge["center"], tint)
            canvas = self.overlay(canvas, f"public/assets/skill_icons/Potential_{shape}_A.png", badge["scale"], badge["center"])
        return np.clip(np.rint(canvas), 0, 255).astype(np.uint8)


def strata(entries, full_sources, model_manifest):
    reference = read(WORK / "calibration-v2-r3/potential-manifest.json")
    ref_entries = {str(e["potential_id"]): e for e in reference["entries"]}
    declared_pids = {Path(path).stem for path in model_manifest["source_receipts"]}
    positive_pids = {pid for pid in declared_pids if ref_entries[pid]["meta"]["corner"] > 0}
    def source_keys(pids):
        return {(ref_entries[pid]["meta"]["glyph"], reference["sources"]["sstoy:" + ref_entries[pid]["meta"]["glyph"]]["sha256"]) for pid in pids}
    positive, declared, cached = source_keys(positive_pids), source_keys(declared_pids), source_keys(set(ref_entries))
    full = {(e["meta"]["glyph"], full_sources["sstoy:" + e["meta"]["glyph"]]["sha256"]) for e in entries}
    if (len(full), len(positive), len(declared), len(cached)) != (433, 39, 41, 150):
        raise ValueError("Unexpected source partition counts")
    return positive, declared, cached, {
        "positive_weight_source_pids": len(positive_pids), "positive_weight_glyph_paths": len(positive),
        "positive_weight_unseen_glyph_paths": len(full - positive),
        "declared_receipt_pids": len(declared_pids), "declared_receipt_glyph_paths": len(declared),
        "declared_receipt_unseen_glyph_paths": len(full - declared),
        "reference_cache_glyph_paths": len(cached), "reference_cache_unseen_glyph_paths": len(full - cached),
        "full_glyph_paths": len(full),
        "full_unique_glyph_blob_hashes": len({full_sources["sstoy:" + path]["git_blob_sha"] for path, _ in full}),
        "meaning": "Unseen main-glyph source context; badge shapes and badge sprites are shared, not held-out novel symbols.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, default=WORK / "synthetic-validation-v1/assets")
    parser.add_argument("--output-dir", type=Path, default=WORK / "synthetic-validation-v1")
    parser.add_argument("--pilot-count", type=int, default=0)
    args = parser.parse_args()
    import cv2
    import numpy as np
    from config import ROIS, REFERENCE_WIDTH
    from src.character_catalog import load_catalog
    from src.load_build import BuildLoader
    from src.load_image import _load_face_icons, _load_skill_images
    from src.load_resolution import get_capture_area
    from src.sstoy_loader.formatter import save_game_preset_to_json
    from src.worker import MatcherWorker
    logging.disable(logging.CRITICAL)
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    suffix = "pilot" if args.pilot_count else "benchmark"
    report_path, csv_path = root / f"{suffix}-report.json", root / f"{suffix}-results.csv"
    if report_path.exists() or csv_path.exists():
        raise ValueError("Refusing to replace an existing result")
    manifest_path = args.assets_dir / "potential-manifest.json"
    manifest = read(manifest_path)
    entries = manifest["entries"]
    if len(entries) != 1160 or len({e["character_key"] for e in entries}) != 40:
        raise ValueError("Full 1160-PID/40-character candidate corpus is required")
    catalog = load_catalog()
    skills = _load_skill_images(args.assets_dir / "potentials")
    if len(skills) != 40 or sum(map(len, skills.values())) != 1160:
        raise ValueError("Actual loader did not load the complete candidate corpus")
    model_path = REPO / "app/resources/models/badge-model.json"
    model_manifest = read(model_path)
    if model_manifest["weights"]["sha256"] != MODEL_SHA:
        raise ValueError("Frozen model changed")
    positive_sources, declared_sources, cache_sources, partition = strata(entries, manifest["sources"], model_manifest)
    protected = list((REPO / "app/resources/templates").rglob("*")) + list((REPO / "app/resources/models").rglob("*"))
    protected += list((args.assets_dir / "potentials").rglob("*.png"))
    protected += [manifest_path, REPO / "app/resources/characters.json", root / "protocol.json", root / "production-before.json"]
    protected += [REPO / "app/src" / p for p in ["worker.py", "load_image.py", "card_matcher.py", "badge_model.py", "load_build.py", "ingame_preset.py", "sstoy_loader/formatter.py"]]
    protected += [Path(s["local_path"]) for s in manifest["sources"].values()]
    before = file_snapshot(protected)
    by_pid = {str(e["potential_id"]): e for e in entries}
    loaders, native_receipts, cases, aliases = {}, [], [], []
    build_root = root / "native-builds"
    build_root.mkdir(exist_ok=True)
    for cid, character in sorted(catalog["characters"].items(), key=lambda item: int(item[0])):
        for role in ["main", "assist"]:
            code, expected_targets = encode_native(catalog, int(cid), role)
            ok, path = save_game_preset_to_json(code, str(build_root), f"synthetic-{cid}-{role}")
            if not ok:
                raise ValueError(path)
            loader = BuildLoader(path)
            if loader.target_level_map != expected_targets:
                raise ValueError("Independently encoded native targets changed during import")
            pids = [str(p) for group, _ in groups(character, role) for p in group]
            if set(loader.get_recognition_potentials(character["key"])) != set(pids) or len(pids) != 16:
                raise ValueError("Genuine native role filter omitted a PID")
            zero_pids = [pid for pid in pids if expected_targets[character["key"]][pid] == 0]
            if not zero_pids:
                raise ValueError("Cyclic native code must retain zero targets")
            loaders[(character["key"], role)] = loader
            native_receipts.append({"character_id": int(cid), "key": character["key"], "role": role,
                                    "code": code, "path": path, "sha256": sha(Path(path)),
                                    "role_count": 16, "zero_pids": zero_pids, "targets": expected_targets[character["key"]]})
            visual_groups = defaultdict(list)
            for pid in pids:
                record = skills[character["key"]][pid + ".png"]
                visual_groups[pixel_key(record["bgr"])].append(pid)
            for pkey, members in visual_groups.items():
                if len(members) > 1:
                    aliases.append({"character_key": character["key"], "role": role, "pids": members, "canonical_pixels": pkey})
            for pid in pids:
                entry = by_pid[pid]
                key = pixel_key(skills[character["key"]][pid + ".png"]["bgr"])
                cases.append({"entry": entry, "role": role, "aliases": visual_groups[key], "visual_key": key})
    if len(cases) != 1280 or sum(len(c["aliases"]) > 1 for c in cases) != 10 or len(aliases) != 5:
        raise ValueError("Unexpected main/assist/common or alias counts")
    plan = {"seed": SEED, "identity_controls": 1280, "independent_scenarios": SCENARIOS,
            "independent_role_cases_per_scenario": 1280, "independent_total": 1280 * len(SCENARIOS),
            "safe_reject_scenarios": NEGATIVES, "safe_reject_contexts": 80, "safe_reject_total": 320,
            "renderer": "NumPy float64 inverse bilinear / premultiplied alpha / final rounding; no generator imports",
            "randomness": "visual-pixel-key plus scenario, never PID, so same-role visual aliases receive identical variations",
            "inference": "known character explicitly supplied; fixed-window ROI; candidate geometry only, never GT bbox/PID/Corner",
            "negative_legality": "blank lacks any glyph; desaturated observation lacks mandatory catalog palette; no legal glyph/Corner/rarity swaps used"}
    plan_bytes = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode()
    plan_path = root / "scenario-plan.json"
    if plan_path.exists() and plan_path.read_bytes() != plan_bytes:
        raise ValueError("Scenario plan already exists with different settings")
    if not plan_path.exists():
        plan_path.write_bytes(plan_bytes)
    work = [(case, {"id": "identity_copy", "kind": "identity_control"}) for case in cases]
    work += [(case, scenario) for case in cases for scenario in SCENARIOS]
    representatives = {}
    for case in cases:
        representatives.setdefault((case["entry"]["character_key"], case["role"]), case)
    work += [(case, {"id": scenario, "kind": "safe_reject"}) for case in representatives.values() for scenario in NEGATIVES]
    total_planned = len(work)
    if args.pilot_count:
        indexes = np.linspace(0, len(work) - 1, min(args.pilot_count, len(work)), dtype=int)
        work = [work[index] for index in indexes]
    renderer = Renderer(manifest["sources"])
    worker = MatcherWorker("")
    if worker.badge_model is None or worker.badge_model.model_sha256 != MODEL_SHA:
        raise ValueError("Actual worker failed to load the frozen model")
    signals, debug = [], []
    worker.match_signal.connect(lambda *values: signals.append(values))
    worker.debug_signal.connect(lambda *values: debug.append(values))
    frame_geometry = {"x": 0, "y": 0, "w": 1280, "h": 720}
    base_area = get_capture_area(frame_geometry, ROIS[0])
    rows, failures = [], []
    start = time.monotonic()
    failure_root = root / ("pilot-failures" if args.pilot_count else "failures")
    for number, (case, scenario) in enumerate(work):
        entry, role = case["entry"], case["role"]
        pid, character = str(entry["potential_id"]), entry["character_key"]
        random_seed = int.from_bytes(hashlib.sha256(f"{SEED}:{case['visual_key']}:{scenario['id']}".encode()).digest()[:8], "big")
        rng = np.random.default_rng(random_seed)
        if scenario["kind"] == "identity_control":
            card = skills[character][pid + ".png"]["bgr"].copy()
        else:
            card = renderer.render(entry, scenario)
        pixel_equivalent = pixel_key(card) == case["visual_key"]
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
        frame = np.zeros((720, 1280, 3), np.uint8)
        h, w = card.shape[:2]
        left, top = base_area["left"] + (base_area["width"] - w) // 2, base_area["top"] + 35
        frame[top:top + h, left:left + w] = card
        if scenario["id"].startswith("blank_"):
            value = {"blank_black": 0, "blank_gray": 128, "blank_white": 255}[scenario["id"]]
            frame[:] = value
        width = scenario.get("window_width", 1280)
        if width != 1280:
            frame = cv2.resize(frame, (width, round(720 * width / 1280)), interpolation=cv2.INTER_LINEAR)
        if scenario.get("jpeg_quality"):
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, scenario["jpeg_quality"]])
            if not ok:
                raise ValueError("JPEG perturbation failed")
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        geometry = {"x": 0, "y": 0, "w": frame.shape[1], "h": frame.shape[0]}
        worker.build_loader = loaders[(character, role)]
        signals.clear(); debug.clear()
        tick = time.monotonic()
        # No expected PID, Corner, render bbox or expected class reaches inference.
        worker.detect_skill(SavedCapture(cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)), geometry, ROIS[0],
                            character, skills, REFERENCE_WIDTH / geometry["w"], 0)
        infer_seconds = time.monotonic() - tick
        if len(signals) != 1:
            raise ValueError("Card-only worker emitted an invalid number of decisions")
        _, text, score, matched, priority, target = signals[0]
        predicted = text[:-4] if matched and text.endswith(".png") and text[:-4].isdigit() else None
        ambiguous = len(case["aliases"]) > 1
        if scenario["kind"] == "safe_reject":
            outcome = "safe_reject" if predicted is None else "unsafe_confirmation"
        elif ambiguous:
            outcome = "ambiguity_refused" if predicted is None else "ambiguity_confirmed"
        else:
            outcome = "unknown" if predicted is None else ("correct" if predicted == pid else "wrong")
        source_key = (entry["meta"]["glyph"], manifest["sources"]["sstoy:" + entry["meta"]["glyph"]]["sha256"])
        expected_target = loaders[(character, role)].get_target_level(character, pid + ".png")
        row = {"case": number, "kind": scenario["kind"], "scenario": scenario["id"], "character_id": entry["character_id"],
               "character_key": character, "role": role, "potential_role": entry["role"], "expected_pid": pid,
               "predicted_pid": predicted, "outcome": outcome, "expected_ambiguity": ambiguous,
               "alias_pids": "|".join(case["aliases"]), "score": score, "target_level": target,
               "expected_target_level": expected_target, "expected_zero_target": expected_target == 0,
               "target_preserved_if_correct": target == expected_target if predicted == pid else None,
               "positive_weight_source_seen": source_key in positive_sources,
               "declared_receipt_source_seen": source_key in declared_sources,
               "reference_cache_source_seen": source_key in cache_sources,
               "source_path": source_key[0], "source_sha256": source_key[1],
               "raw_nominal_pixel_equivalent_to_asset": pixel_equivalent,
               "inference_seconds": infer_seconds, "variation_seed": random_seed}
        rows.append(row)
        if outcome in ("wrong", "unknown", "ambiguity_confirmed", "unsafe_confirmation"):
            failure_root.mkdir(exist_ok=True)
            stem = f"{number:05d}-{character}-{role}-{pid}-{scenario['id']}"
            image_path = failure_root / (stem + ".png")
            # Preserve normalized capture only; no UID, live capture or extraneous desktop.
            area = get_capture_area(geometry, ROIS[0])
            patch = SavedCapture(frame).grab(area)
            cv2.imencode(".png", patch)[1].tofile(image_path)
            record = {"case": row, "signals": [list(value) for value in signals], "debug": [list(value) for value in debug],
                      "scenario_parameters": scenario, "source_refs": entry["source_refs"],
                      "image": str(image_path), "image_sha256": sha(image_path)}
            log_path = failure_root / (stem + ".json")
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            failures.append({"case": number, "outcome": outcome, "image": str(image_path), "log": str(log_path)})
        if (number + 1) % 500 == 0:
            print(json.dumps({"completed": number + 1, "total": len(work), "elapsed_seconds": time.monotonic() - start}), flush=True)
    elapsed = time.monotonic() - start
    after = file_snapshot(protected)
    changed = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
    summary = {}
    for kind in ["identity_control", "independent", "safe_reject"]:
        subset = [r for r in rows if r["kind"] == kind]
        summary[kind] = {"cases": len(subset), "outcomes": dict(Counter(r["outcome"] for r in subset)),
                         "resolvable_cases": sum(not r["expected_ambiguity"] for r in subset),
                         "expected_ambiguity_cases": sum(r["expected_ambiguity"] for r in subset)}
    stratified = {}
    for field in ["positive_weight_source_seen", "declared_receipt_source_seen", "reference_cache_source_seen"]:
        stratified[field] = {str(value): dict(Counter(r["outcome"] for r in rows if r["kind"] == "independent" and r[field] == value)) for value in [True, False]}
    with csv_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    report = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "synthetic_card_only_actual_detect_skill_with_supplied_character_context_no_face_accuracy",
              "pilot": bool(args.pilot_count), "seed": SEED, "scenario_plan": plan, "scenario_plan_sha256": sha(plan_path),
              "corpus": {"characters": 40, "unique_pids": 1160, "role_cases": 1280,
                         "main_exclusive": 520, "assist_exclusive": 520, "common_unique": 120, "common_both_role_cases": 240,
                         "resolvable_role_cases": 1270, "same_role_ambiguous_cases": 10, "same_role_alias_groups": aliases},
              "glyph_source_partitions": partition, "summary": summary, "stratified_independent": stratified,
              "per_scenario": {s: dict(Counter(r["outcome"] for r in rows if r["scenario"] == s)) for s in sorted({r['scenario'] for r in rows})},
              "native_import_receipts": native_receipts, "inference_input_policy": "Known char and native role context are explicit. Expected PID/Corner/render bbox only construct observations or score results; never passed to matcher/model.",
              "elapsed_seconds": elapsed, "mean_inference_seconds": sum(r["inference_seconds"] for r in rows) / len(rows),
              "planned_total_cases": total_planned, "executed_cases": len(rows),
              "pilot_projected_full_seconds": elapsed / len(rows) * total_planned if args.pilot_count else None,
              "csv": {"path": str(csv_path), "sha256": sha(csv_path)}, "failure_cases": failures,
              "protected_sources_before": before, "protected_sources_after": after, "changed_protected_files": changed,
              "production_model_sources_unchanged": not changed, "script_sha256": sha(Path(__file__)),
              "limits": ["Identity-copy controls verify loading and unambiguous lookup, not recognition accuracy on game images.",
                         "Independent rendering shares public sprites and numeric geometry assumptions; it is synthetic stress coverage, not the real game renderer.",
                         "Known character is supplied; no face, skin, live UI, complete app or real-world accuracy claim is made.",
                         "Expected alias refusal and safe rejection are separate from correct-PID accuracy. Ordinary unknowns never count as correct.",
                         "Main-glyph source holdout reuses familiar badge types; it does not demonstrate rejection of every unseen symbol.",
                         "No training, parameter adjustment or production registration occurs."]}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "sha256": sha(report_path), "summary": summary,
                      "elapsed_seconds": elapsed, "projected_full_seconds": report["pilot_projected_full_seconds"], "protected_unchanged": not changed}, ensure_ascii=True), flush=True)
    if changed:
        raise RuntimeError("Protected source changed during benchmarking")


if __name__ == "__main__":
    main()
