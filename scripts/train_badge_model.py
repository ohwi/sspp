"""Train and freeze a tiny CPU badge probe; never change application resources.

Synthesized templates and eight even-index fit screenshots are training inputs.
Odd-index test pixels are opened only after the weights/manifest are frozen.
No expected character or PID is supplied to test inference.
"""
import argparse
from pathlib import Path
import hashlib
import io
import json
import logging
import sys
import time

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
WORK = ROOT.parent
HERE = None
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))
import cv2
import numpy as np
from badge_model_features import BadgeProbe, CLASSES, FEATURE_SPEC, KEY_SHAPES, THRESHOLDS, extract, features
from calibrate_potential_templates import load_roi
from config import FACE_OFFSET, REFERENCE_WIDTH, ROIS
from src.card_matcher import match_card
from src.load_image import _load_face_icons
from src.load_resolution import get_capture_area
from src.worker import MatcherWorker

SEED = 20261012
ROLES = {"allie": "main", "donna": "assist", "suntide_willow": "assist"}
logging.disable(logging.CRITICAL)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, raw):
    path = Path(path)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Refusing to overwrite a frozen artifact: " + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def write_json(path, data):
    write(path, (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def decode(path):
    return cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def augment(crop, rng):
    h, w = crop.shape[:2]
    angle = float(rng.uniform(-4, 4))
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, float(rng.uniform(.88, 1.12)))
    matrix[:, 2] += rng.uniform(-3, 3, 2)
    image = cv2.warpAffine(crop, matrix, (w, h), borderMode=cv2.BORDER_REFLECT_101)
    if rng.random() < .5:
        image = cv2.GaussianBlur(image, (3, 3), float(rng.uniform(.2, .9)))
    image = image.astype(np.float32) * rng.uniform(.80, 1.20) + rng.uniform(-15, 15)
    image += rng.normal(0, rng.uniform(0, 5), image.shape)
    return np.clip(image, 0, 255).astype(np.uint8)


def negative(rng, kind):
    # Stars are deliberately absent: the independent reviewer reserves one
    # unseen star shape as an out-of-distribution challenge.
    h = w = 58
    base = rng.uniform(45, 205, 3)
    gradient = np.linspace(-25, 25, h)[:, None, None]
    image = np.clip(np.ones((h, w, 3)) * base + gradient, 0, 255).astype(np.uint8)
    white = (245, 245, 245)
    if kind == "square":
        cv2.rectangle(image, (18, 18), (40, 40), white, 3)
    elif kind == "cross":
        cv2.line(image, (17, 29), (41, 29), white, 4)
        cv2.line(image, (29, 17), (29, 41), white, 4)
    elif kind == "filled_blob":
        cv2.ellipse(image, (29, 29), (14, 9), float(rng.integers(0, 180)), 0, 360, white, -1)
    elif kind == "lines":
        for _ in range(3):
            p1, p2 = tuple(rng.integers(3, 55, 2)), tuple(rng.integers(3, 55, 2))
            cv2.line(image, p1, p2, white, int(rng.integers(1, 4)))
    elif kind == "glyph_tail":
        cv2.ellipse(image, (29, -7), (30, 30), 0, 0, 180, white, 4)
        cv2.line(image, (3, 4), (54, 15), white, 3)
    return augment(image, rng)


class Capture:
    def __init__(self, image):
        self.image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    def grab(self, area):
        x, y = area["left"], area["top"]
        return self.image[y:y + area["height"], x:x + area["width"]]


class Context:
    def get_recognition_characters(self):
        return set(ROLES)  # User preset slots, never screenshot labels.


def predict_face(input_row, faces):
    image = decode(input_row["frozen"])
    if image.shape[:2] == (932, 1602):
        image = image[31:931, 1:1601]
    geo = {"x": 0, "y": 0, "w": image.shape[1], "h": image.shape[0]}
    worker = MatcherWorker("")
    worker.build_loader = Context()
    return [worker.detect_face(Capture(image), geo, roi, faces, REFERENCE_WIDTH / geo["w"])
            for roi in ROIS]


def pool_ids(character, role):
    s = character["preset_schema"]
    prefix = "Master" if role == "main" else "Assist"
    return set(map(str, s[prefix + "SpecificPotentialIds"] + s[prefix + "NormalPotentialIds"] + s["CommonPotentialIds"]))


def infer_slot(roi, predicted_char, pools, allowed, model=None):
    if predicted_char not in pools:
        return {"reason": "face_rejected", "valid": False}, None
    filename, score, details = match_card(roi, predicted_char, pools, allowed)
    best = details.get("best") or (details.get("candidates") or [None])[0]
    if not best:
        return {"reason": "no_card_alignment", "valid": False}, None
    record = pools[predicted_char][best["filename"]]
    x, y = best["location"]
    h, w = record["gray"].shape
    patch = roi[y:y + h, x:x + w]
    result = model.classify_badge(patch, {"corner_region": record["meta"]["corner_region"]}) if model else {}
    result.update({"baseline_filename": filename, "baseline_score": score,
                   "candidate_filename": best["filename"], "glyph_score": best.get("glyph_score"),
                   "hue_distance": best.get("hue_distance"), "location": [x, y],
                   "region": record["meta"]["corner_region"], "baseline_reason": details["reason"]})
    return result, patch


def main():
    global HERE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Fresh, non-existing scratch directory outside the repository")
    parser.add_argument("--input-dir", type=Path, default=WORK / "screenshot-validation")
    parser.add_argument("--template-dir", type=Path, default=WORK / "calibration-v2-r3")
    parser.add_argument("--catalog", type=Path, default=ROOT / "app/resources/characters.json")
    parser.add_argument("--production-dir", type=Path, default=ROOT / "app/resources/templates")
    args = parser.parse_args()
    HERE = args.output_dir.resolve()
    if HERE == ROOT or ROOT in HERE.parents:
        parser.error("Output must be outside the application repository")
    if HERE.exists():
        parser.error("Output already exists; frozen artifacts are never overwritten")
    validation = args.input_dir.resolve()
    template_dir = args.template_dir.resolve()
    if not (validation / "inputs.json").is_file() or not (template_dir / "potential-manifest.json").is_file():
        parser.error("Frozen screenshot inputs and calibrated source manifest are required")
    for protected_root in (validation, template_dir, args.production_dir.resolve()):
        if HERE == protected_root or protected_root in HERE.parents:
            parser.error("Output must not be inside protected screenshot/template input directories")
    HERE.mkdir(parents=True, exist_ok=False)
    write(HERE / "badge_probe_runtime.py", (SCRIPTS / "badge_model_features.py").read_bytes())
    rng = np.random.default_rng(SEED)
    start = time.perf_counter()
    inputs = sorted(read(validation / "inputs.json")["files"], key=lambda r: r["id"])
    fit_ids, test_ids = [x["id"] for x in inputs[::2]], [x["id"] for x in inputs[1::2]]
    truth = {r["id"]: r["cards"] for n in ("ground-truth-a.json", "ground-truth-b.json")
             for r in read(validation / n)["files"]}
    catalog = read(args.catalog.resolve())
    chars = {r["key"]: r for r in catalog["characters"].values()}
    manifest_path = template_dir / "potential-manifest.json"
    source_manifest = read(manifest_path)
    pools, synth = {}, {1: [], 2: [], 3: []}
    source_receipts = {}
    for entry in source_manifest["entries"]:
        if entry["character_key"] not in ROLES:
            continue
        path = template_dir / entry["relative_path"]
        if sha(path) != entry["template_sha256"]:
            raise ValueError("Template hash mismatch")
        image, meta = decode(path), entry["meta"]
        pools.setdefault(entry["character_key"], {})[entry["potential_id"] + ".png"] = {
            "gray": cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), "bgr": image, "meta": meta}
        if meta["corner"]:
            crop, reason = extract(image, {"corner_region": meta["corner_region"]})
            if reason:
                raise ValueError(reason)
            synth[meta["corner"]].append(crop)
        source_receipts[str(path)] = sha(path)
    # Protect production input bytes, but do not use the cache-writing loader.
    production = args.production_dir.resolve()
    protected = {str(p): sha(p) for p in sorted(production.rglob("*.png"))}
    faces = _load_face_icons(production / "icons")
    real = []
    for input_row in inputs[::2]:
        for label in truth[input_row["id"]]:
            record = pools[label["character_key"]][label["potential_id"] + ".png"]
            # Training alignment may use the fit annotation. It never receives
            # an odd-index test image or a test PID.
            roi = load_roi(input_row, label["index"])
            template = record["gray"]
            search = cv2.matchTemplate(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), template[:105], cv2.TM_CCOEFF_NORMED)
            search = search[:roi.shape[0] - template.shape[0] + 1]
            y, x = np.unravel_index(np.argmax(search), search.shape)
            patch = roi[y:y + template.shape[0], x:x + template.shape[1]]
            crop, reason = extract(patch, {"corner_region": record["meta"]["corner_region"]})
            if reason:
                raise ValueError("Fit annotation crop failed: " + reason)
            real.append((crop, record["meta"]["corner"]))
    print("Preparing balanced synthetic sprites and even-index fit-photo augmentations", flush=True)
    crops, labels = [], []
    negative_kinds = ["blank", "square", "cross", "filled_blob", "lines", "glyph_tail"]
    for corner in range(4):
        for index in range(1600):
            if corner == 0:
                crop = negative(rng, negative_kinds[index % len(negative_kinds)])
            else:
                crop = augment(synth[corner][int(rng.integers(len(synth[corner])))], rng)
            crops.append(crop); labels.append(corner)
    for crop, corner in real:
        for _ in range(80):
            crops.append(augment(crop, rng)); labels.append(corner)
    x = np.stack([features(crop) for crop in crops])
    y = np.asarray(labels, np.int64)
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), .05)
    x = (x - mean) / scale
    w = {"w1": (rng.normal(size=(324, 64)) * np.sqrt(2 / 324)).astype(np.float32),
         "b1": np.zeros(64, np.float32),
         "w2": (rng.normal(size=(64, 4)) * np.sqrt(2 / 64)).astype(np.float32),
         "b2": np.zeros(4, np.float32)}
    m = {key: np.zeros_like(v) for key, v in w.items()}
    v = {key: np.zeros_like(value) for key, value in w.items()}
    step = 0; history = []
    for epoch in range(30):
        order = rng.permutation(len(y)); losses = []
        for begin in range(0, len(y), 256):
            ix = order[begin:begin + 256]; batch = x[ix]
            hidden = np.maximum(batch @ w["w1"] + w["b1"], 0)
            logits = hidden @ w["w2"] + w["b2"]
            exp = np.exp(logits - logits.max(axis=1, keepdims=True)); probs = exp / exp.sum(axis=1, keepdims=True)
            losses.append(float(-np.log(np.maximum(probs[np.arange(len(ix)), y[ix]], 1e-9)).mean()))
            delta = probs.copy(); delta[np.arange(len(ix)), y[ix]] -= 1; delta /= len(ix)
            dh = (delta @ w["w2"].T) * (hidden > 0)
            grads = {"w2": hidden.T @ delta + 1e-4 * w["w2"], "b2": delta.sum(axis=0),
                     "w1": batch.T @ dh + 1e-4 * w["w1"], "b1": dh.sum(axis=0)}
            step += 1
            for key in w:
                m[key] = .9 * m[key] + .1 * grads[key]
                v[key] = .999 * v[key] + .001 * grads[key] ** 2
                w[key] -= .001 * (m[key] / (1 - .9 ** step)) / (np.sqrt(v[key] / (1 - .999 ** step)) + 1e-8)
        history.append({"epoch": epoch + 1, "loss": float(np.mean(losses))})
        if (epoch + 1) % 5 == 0:
            print("epoch", epoch + 1, "loss", round(history[-1]["loss"], 5), flush=True)
    w.update(mean=mean.astype(np.float32), scale=scale.astype(np.float32))
    buffer = io.BytesIO(); np.savez_compressed(buffer, **w)
    write(HERE / "weights.npz", buffer.getvalue())
    model_manifest = {
        "schema_version": 1, "status": "probe_not_registered", "model_type": "numpy_mlp",
        "weights": {"file": "weights.npz", "sha256": sha(HERE / "weights.npz"),
                    "bytes": (HERE / "weights.npz").stat().st_size, "keys": list(KEY_SHAPES), "shapes": KEY_SHAPES},
        "feature_spec": FEATURE_SPEC, "classes": CLASSES, "thresholds": THRESHOLDS,
        "architecture": {"input": 324, "hidden": 64, "output": 4, "activation": "relu", "output_activation": "softmax"},
        "parameter_count": 21060, "normalization_value_count": 648,
        "training": {"seed": SEED, "epochs": 30, "batch_size": 256, "optimizer": "Adam",
                     "fit_ids": fit_ids, "test_ids": test_ids, "test_used_for_training_or_selection": False,
                     "synthetic_samples": 6400, "fit_augmented_samples": len(real) * 80,
                     "fit_real_badges": len(real), "negative_shapes": negative_kinds,
                     "held_out_challenge_shape": "star_not_used", "source_manifest_sha256": sha(manifest_path)},
        "source_receipts": source_receipts,
        "runtime_helper_sha256": sha(HERE / "badge_probe_runtime.py"),
        "training_script_sha256": sha(Path(__file__)),
        "libraries": {"numpy": np.__version__, "opencv": cv2.__version__},
        "limits": ["Softmax confidence is not a calibrated correctness probability.",
                   "Closed-set classification cannot guarantee rejection of every unseen shape.",
                   "Only three characters and 31 PIDs occur in the saved photos.",
                   "This is a badge classifier probe, not complete PID or end-to-end validation.",
                   "Test screenshots were previously inspected in discovery; this is not fresh generalization evidence."]}
    # Freeze before opening the test-fold pixels for inference.
    write_json(HERE / "manifest.json", model_manifest)
    model = BadgeProbe(HERE)
    predictions = []; summaries = {}
    for split, selected in (("fit", inputs[::2]), ("test", inputs[1::2])):
        summary = {"correct_corner": 0, "wrong_corner": 0, "invalid": 0, "face_correct": 0}
        for input_row in selected:
            detected = predict_face(input_row, faces)
            for index in range(3):
                predicted_char, diff = detected[index]
                if diff > .15:
                    predicted_char = None
                roi = load_roi(input_row, index)
                allowed = pool_ids(chars[predicted_char], ROLES[predicted_char]) if predicted_char in ROLES else set()
                result, _ = infer_slot(roi, predicted_char, pools, allowed, model)
                # Labels enter only here, after actual face/family/shape inference.
                label = next(x for x in truth[input_row["id"]] if x["index"] == index)
                expected_record = pools[label["character_key"]][label["potential_id"] + ".png"]
                expected_corner = expected_record["meta"]["corner"]
                summary["face_correct"] += predicted_char == label["character_key"]
                if not result.get("valid"):
                    summary["invalid"] += 1
                elif result["corner"] == expected_corner:
                    summary["correct_corner"] += 1
                else:
                    summary["wrong_corner"] += 1
                predictions.append({"split": split, "image_id": input_row["id"], "index": index,
                                    "predicted_character": predicted_char, "expected_corner": expected_corner,
                                    "expected_pid_for_evaluation_only": label["potential_id"], **result})
        summaries[split] = summary
    # Cost measured on a fit-photo patch, not used for changing the model.
    sample, _ = extract(np.pad(real[0][0], ((0, 0), (0, 0), (0, 0))), {"corner_region": [0, 0, real[0][0].shape[1], real[0][0].shape[0]]})
    tick = time.perf_counter()
    for _ in range(500):
        model.probabilities([sample])
    inference_ms = (time.perf_counter() - tick) * 1000 / 500
    if any(sha(path) != value for path, value in protected.items()):
        raise ValueError("Protected production assets changed")
    report = {"schema_version": 1, "scope": "badge-only probe with inferred face and glyph-family alignment",
              "summaries": summaries, "predictions": predictions, "training_history": history,
              "training_samples": len(y), "elapsed_seconds": time.perf_counter() - start,
              "mean_inference_ms": inference_ms, "production_unchanged": True,
              "test_evaluations_after_freeze": 1, "manifest_sha256": sha(HERE / "manifest.json")}
    write_json(HERE / "training-report.json", report)
    print("Frozen model probe", model_manifest["weights"], flush=True)
    print("Badge-only summaries", summaries, "inference_ms", inference_ms, flush=True)


if __name__ == "__main__":
    main()
