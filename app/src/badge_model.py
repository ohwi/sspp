"""Small badge-only HOG/MLP observer; candidate IDs never enter its features.

Weights are verified once and copied into immutable memory. This module reports
badge observations; the card matcher separately enforces glyph, color, role,
duplicate and confidence/margin requirements before accepting any potential.
"""
import hashlib
import io
import json
import logging
from pathlib import Path, PureWindowsPath
import zipfile

import cv2
import numpy as np

from config import RESOURCES_DIR

logger = logging.getLogger("BadgeModel")
MAX_MODEL_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MODEL_SHAPES = {
    "w1": (324, 64), "b1": (64,), "w2": (64, 4), "b2": (4,),
    "mean": (324,), "scale": (324,),
}
FEATURE_SPEC = {
    "kind": "opencv_hog", "image_size": [32, 32], "channels": "BGR_to_gray",
    "resize": "INTER_AREA", "feature_count": 324,
    "win_size": [32, 32], "block_size": [16, 16],
    "block_stride": [8, 8], "cell_size": [8, 8], "bins": 9,
    "deriv_aperture": 1, "win_sigma": -1,
    "histogram_norm_type": 0, "l2_hys_threshold": 0.2,
    "gamma_correction": False, "nlevels": 64, "signed_gradient": False,
    "normalization": "(features - mean) / scale", "scale_floor": 0.05,
    "crop_field": "corner_region", "uses_expected_corner_or_pid": False,
}
CLASSES = ["background", "Diamond", "Triangle", "Round"]
THRESHOLDS = {"min_contrast": 15, "confidence": 0.95, "margin": 0.50}
ARCHITECTURE = {"input": 324, "hidden": 64, "output": 4,
                "activation": "relu", "output_activation": "softmax"}


def _exact_json(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _bounded_bytes(path, limit):
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Model resource exceeds its size limit")
    return data


def _checked_weights(raw):
    # Validate size and headers before NumPy can allocate an unexpected array.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        expected = {key + ".npy" for key in MODEL_SHAPES}
        if (len(entries) != len(expected) or {entry.filename for entry in entries} != expected
                or sum(entry.file_size for entry in entries) > MAX_MODEL_BYTES):
            raise ValueError("Unexpected weight archive entries or size")
        for entry in entries:
            if entry.flag_bits & 1 or entry.is_dir():
                raise ValueError("Unsupported weight archive entry")
            with archive.open(entry) as handle:
                version = np.lib.format.read_magic(handle)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(handle)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(handle)
                else:
                    raise ValueError("Unsupported weight array header")
                if shape != MODEL_SHAPES[entry.filename[:-4]] or dtype != np.dtype(np.float32):
                    raise ValueError("Unexpected weight dtype or shape")
    weights = {}
    with np.load(io.BytesIO(raw), allow_pickle=False) as arrays:
        for key, shape in MODEL_SHAPES.items():
            value = arrays[key]
            if value.shape != shape or value.dtype != np.float32 or not np.all(np.isfinite(value)):
                raise ValueError("Invalid weight dtype, shape or numeric value")
            value = np.array(value, dtype=np.float32, copy=True)
            value.setflags(write=False)
            weights[key] = value
    if np.any(weights["scale"] < np.float32(0.05)):
        raise ValueError("Invalid feature normalization scale")
    return weights


def _invalid(reason):
    return {"valid": False, "corner": 0, "probs": [0.0] * 4,
            "top_margin": 0.0, "reason": reason}


class BadgeModel:
    """HOG324 -> normalized features -> ReLU64 -> four-class softmax."""
    def __init__(self, weights, model_sha256, manifest_sha256):
        self._weights = weights
        self.model_sha256 = model_sha256
        self.manifest_sha256 = manifest_sha256
        self._hog = cv2.HOGDescriptor((32, 32), (16, 16), (8, 8), (8, 8),
                                      9, 1, -1, 0, 0.2, False, 64, False)

    def classify_badge(self, patch, meta):
        # Only geometry is read. Corner/PID/glyph/target values cannot affect the
        # observed class or become a label disguised as an input feature.
        if (not isinstance(patch, np.ndarray) or patch.dtype != np.uint8
                or patch.ndim != 3 or patch.shape[2] not in (3, 4) or not patch.size):
            return _invalid("missing_or_invalid_color")
        if not isinstance(meta, dict):
            return _invalid("invalid_corner_region")
        region = meta.get("corner_region")
        if (not isinstance(region, (list, tuple)) or len(region) != 4
                or any(type(value) is not int for value in region)):
            return _invalid("invalid_corner_region")
        x1, y1, x2, y2 = region
        if not (0 <= x1 < x2 <= patch.shape[1] and 0 <= y1 < y2 <= patch.shape[0]):
            return _invalid("invalid_corner_region")
        try:
            crop = patch[y1:y2, x1:x2, :3]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            if int(np.ptp(gray)) < THRESHOLDS["min_contrast"]:
                return _invalid("low_contrast")
            gray = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
            features = self._hog.compute(gray).reshape(-1)
            if features.shape != (324,) or not np.all(np.isfinite(features)):
                return _invalid("invalid_features")
            weights = self._weights
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                normalized = (features - weights["mean"]) / weights["scale"]
                hidden = np.maximum(normalized @ weights["w1"] + weights["b1"], 0)
                logits = hidden @ weights["w2"] + weights["b2"]
            if not np.all(np.isfinite(logits)):
                return _invalid("nonfinite_logits")
            exp = np.exp(logits.astype(np.float64) - float(np.max(logits)))
            probs = exp / np.sum(exp)
            if not np.all(np.isfinite(probs)):
                return _invalid("nonfinite_probabilities")
            corner = int(np.argmax(probs))
            ordered = np.sort(probs)
            margin = float(ordered[-1] - ordered[-2])
            valid = (corner != 0 and float(probs[corner]) >= THRESHOLDS["confidence"]
                     and margin >= THRESHOLDS["margin"])
            reason = "observed_badge" if valid else ("no_badge" if corner == 0 else "low_confidence_or_margin")
            return {"valid": bool(valid), "corner": corner, "probs": probs.tolist(),
                    "top_margin": margin, "reason": reason}
        except (cv2.error, ValueError, TypeError, FloatingPointError):
            return _invalid("invalid_features")


def load_badge_model(models_dir=None):
    """Return an immutable verified model, or None with a useful warning.

    Hash validation happens at load time. A running worker continues using the
    same in-memory weights if files later change; a new load verifies them again.
    """
    try:
        folder = Path(models_dir) if models_dir is not None else Path(RESOURCES_DIR) / "models"
        manifest_raw = _bounded_bytes(folder / "badge-model.json", MAX_MANIFEST_BYTES)
        manifest = json.loads(manifest_raw.decode("utf-8"))
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
                or manifest["schema_version"] != 1 or manifest.get("model_type") != "numpy_mlp"
                or not _exact_json(manifest.get("feature_spec"), FEATURE_SPEC)
                or not _exact_json(manifest.get("classes"), CLASSES)
                or not _exact_json(manifest.get("thresholds"), THRESHOLDS)
                or not _exact_json(manifest.get("architecture"), ARCHITECTURE)
                or type(manifest.get("parameter_count")) is not int or manifest["parameter_count"] != 21060
                or type(manifest.get("normalization_value_count")) is not int
                or manifest["normalization_value_count"] != 648):
            raise ValueError("Unsupported badge model schema or feature policy")
        entry = manifest.get("weights")
        if not isinstance(entry, dict):
            raise ValueError("Missing weight declaration")
        filename = entry.get("file")
        if (not isinstance(filename, str) or not filename.endswith(".npz")
                or filename != Path(filename).name or filename != PureWindowsPath(filename).name
                or "/" in filename or "\\" in filename or ":" in filename):
            raise ValueError("Weight filename must be a local basename")
        expected_shapes = {key: list(shape) for key, shape in MODEL_SHAPES.items()}
        if (entry.get("keys") != list(MODEL_SHAPES)
                or not _exact_json(entry.get("shapes"), expected_shapes)
                or type(entry.get("bytes")) is not int or not 0 < entry["bytes"] <= MAX_MODEL_BYTES):
            raise ValueError("Unsupported weight array contract")
        raw = _bounded_bytes(folder / filename, MAX_MODEL_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != entry["bytes"] or digest != entry.get("sha256"):
            raise ValueError("Badge weights hash or byte size mismatch")
        model = BadgeModel(_checked_weights(raw), digest, hashlib.sha256(manifest_raw).hexdigest())
        logger.info("Badge model loaded (21060 parameters, %s)", digest[:12])
        return model
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, EOFError, UnicodeError) as error:
        logger.warning("Badge model unavailable: %s", error)
        return None
