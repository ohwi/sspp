"""Shared training feature extraction and NumPy badge probe. Softmax scores are not calibrated probabilities."""
from pathlib import Path
import json
import cv2
import numpy as np

HOG = cv2.HOGDescriptor((32, 32), (16, 16), (8, 8), (8, 8), 9, 1, -1, 0, .2, False, 64, False)
FEATURE_SPEC = {
    "kind": "opencv_hog", "image_size": [32, 32], "channels": "BGR_to_gray",
    "resize": "INTER_AREA", "feature_count": 324,
    "win_size": [32, 32], "block_size": [16, 16], "block_stride": [8, 8],
    "cell_size": [8, 8], "bins": 9, "deriv_aperture": 1, "win_sigma": -1,
    "histogram_norm_type": 0, "l2_hys_threshold": .2, "gamma_correction": False,
    "nlevels": 64, "signed_gradient": False,
    "normalization": "(features - mean) / scale", "scale_floor": .05,
    "crop_field": "corner_region", "uses_expected_corner_or_pid": False,
}
CLASSES = ["background", "Diamond", "Triangle", "Round"]
THRESHOLDS = {"confidence": .95, "margin": .50, "min_contrast": 15}
KEY_SHAPES = {"w1": [324, 64], "b1": [64], "w2": [64, 4], "b2": [4],
              "mean": [324], "scale": [324]}


def features(crop):
    gray = cv2.cvtColor(crop[:, :, :3], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    return HOG.compute(small).ravel().astype(np.float32)


def extract(patch, meta):
    if (not isinstance(patch, np.ndarray) or patch.dtype != np.uint8
            or patch.ndim != 3 or patch.shape[2] not in (3, 4)):
        return None, "missing_color"
    if not isinstance(meta, dict):
        return None, "invalid_meta"
    region = meta.get("corner_region")
    if (not isinstance(region, (list, tuple)) or len(region) != 4
            or any(type(x) is not int for x in region)):
        return None, "invalid_meta"
    x1, y1, x2, y2 = region
    if not (0 <= x1 < x2 <= patch.shape[1] and 0 <= y1 < y2 <= patch.shape[0]):
        return None, "invalid_meta"
    crop = patch[y1:y2, x1:x2, :3]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if int(np.ptp(gray)) < THRESHOLDS["min_contrast"]:
        return None, "low_contrast"
    return crop, None


class BadgeProbe:
    def __init__(self, directory):
        self.directory = Path(directory)
        with np.load(self.directory / "weights.npz", allow_pickle=False) as model:
            self.weights = {key: model[key].copy() for key in KEY_SHAPES}
        for key, shape in KEY_SHAPES.items():
            value = self.weights[key]
            if value.dtype != np.float32 or list(value.shape) != shape or not np.all(np.isfinite(value)):
                raise ValueError("Invalid model array")

    def probabilities(self, crops):
        x = np.stack([features(crop) for crop in crops])
        w = self.weights
        x = (x - w["mean"]) / w["scale"]
        hidden = np.maximum(x @ w["w1"] + w["b1"], 0)
        logits = hidden @ w["w2"] + w["b2"]
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        return exp / exp.sum(axis=1, keepdims=True)

    def classify_badge(self, patch, meta):
        crop, reason = extract(patch, meta)
        if reason:
            return {"valid": False, "corner": 0, "probs": [1., 0., 0., 0.],
                    "top_margin": 0., "reason": reason}
        values = self.probabilities([crop])[0]
        order = np.argsort(values)[::-1]
        top = int(order[0])
        margin = float(values[order[0]] - values[order[1]])
        valid = top != 0 and float(values[top]) >= THRESHOLDS["confidence"] and margin >= THRESHOLDS["margin"]
        return {"valid": bool(valid), "corner": top, "probs": [float(x) for x in values],
                "top_margin": margin,
                "reason": "accepted" if valid else "background" if top == 0 else "low_confidence"}
