"""Verified, bounded loading and label-independent badge observations."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src import badge_model


class BadgeModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.image = np.full((100, 100, 3), 30, np.uint8)
        cv2.polylines(self.image, [np.array([[50, 20], [80, 50], [50, 80], [20, 50]])],
                      True, (240, 190, 150), 4)
        self.meta = {"corner_region": [10, 10, 90, 90]}

    def write_model(self, updates=None, manifest_update=None, logits=None):
        arrays = {key: np.zeros(shape, np.float32) for key, shape in badge_model.MODEL_SHAPES.items()}
        arrays["scale"][:] = 1
        arrays["b2"] = np.array([0, 12, 0, 0] if logits is None else logits, np.float32)
        arrays.update(updates or {})
        target = self.folder / "weights.npz"
        np.savez_compressed(target, **arrays)
        raw = target.read_bytes()
        manifest = {
            "schema_version": 1, "model_type": "numpy_mlp",
            "weights": {"file": target.name, "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw), "keys": list(badge_model.MODEL_SHAPES),
                        "shapes": {key: list(shape) for key, shape in badge_model.MODEL_SHAPES.items()}},
            "feature_spec": badge_model.FEATURE_SPEC, "classes": badge_model.CLASSES,
            "thresholds": badge_model.THRESHOLDS, "architecture": badge_model.ARCHITECTURE,
            "parameter_count": 21060, "normalization_value_count": 648,
        }
        if manifest_update:
            manifest_update(manifest)
        (self.folder / "badge-model.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def load(self):
        return badge_model.load_badge_model(self.folder)

    def test_verified_model_returns_finite_distribution_without_using_candidate_labels(self):
        self.write_model()
        model = self.load()
        self.assertIsNotNone(model)
        original = model.classify_badge(self.image, self.meta)
        contradictory = dict(self.meta, corner=3, pid="wrong_pid", glyph="other", target_level=0)
        self.assertEqual(model.classify_badge(self.image, contradictory), original)
        self.assertTrue(original["valid"])
        self.assertEqual(original["corner"], 1)
        self.assertAlmostEqual(sum(original["probs"]), 1)
        self.assertGreater(original["top_margin"], .99)
        bgra = cv2.cvtColor(self.image, cv2.COLOR_BGR2BGRA)
        self.assertEqual(model.classify_badge(bgra, self.meta), original)

    def test_uniform_background_low_margin_and_background_class_are_not_badges(self):
        for logits in ([12, 0, 0, 0], [0, 0, 0, 0], [0, 4, 3.9, 0]):
            with self.subTest(logits=logits):
                self.write_model(logits=logits)
                self.assertFalse(self.load().classify_badge(self.image, self.meta)["valid"])
        self.write_model()
        model = self.load()
        for intensity in (0, 128, 255):
            result = model.classify_badge(np.full_like(self.image, intensity), self.meta)
            self.assertFalse(result["valid"])
            self.assertEqual(result["reason"], "low_contrast")

    def test_bad_input_and_invalid_corner_bounds_fail_without_exceptions(self):
        self.write_model()
        model = self.load()
        images = [None, [], np.zeros((20, 20), np.uint8), np.zeros((20, 20, 1), np.uint8),
                  np.zeros((0, 10, 3), np.uint8), self.image.astype(np.float32)]
        for image in images:
            self.assertFalse(model.classify_badge(image, self.meta)["valid"])
        regions = [None, [], [0, 0, 10], [-1, 0, 20, 20], [0, 0, 101, 20],
                   [20, 0, 10, 20], [0, 0, 0, 20], [0, 0, True, 20], [0., 0, 20, 20]]
        for region in regions:
            self.assertFalse(model.classify_badge(self.image, {"corner_region": region})["valid"])
        self.assertFalse(model.classify_badge(self.image, None)["valid"])

    def test_missing_corrupt_or_hash_mismatched_files_warn_and_disable_model(self):
        with self.assertLogs("BadgeModel", level="WARNING"):
            self.assertIsNone(self.load())
        self.write_model()
        (self.folder / "weights.npz").write_bytes(b"changed")
        with self.assertLogs("BadgeModel", level="WARNING"):
            self.assertIsNone(self.load())
        self.write_model()
        (self.folder / "badge-model.json").write_text("{invalid", encoding="utf-8")
        with self.assertLogs("BadgeModel", level="WARNING"):
            self.assertIsNone(self.load())
        self.write_model()
        (self.folder / "weights.npz").unlink()
        with self.assertLogs("BadgeModel", level="WARNING"):
            self.assertIsNone(self.load())

    def test_invalid_weight_dtype_shape_numeric_values_and_scale_are_rejected(self):
        invalid = [
            {"w1": np.zeros((324, 63), np.float32)},
            {"w1": np.zeros((324, 64), np.float64)},
            {"w1": np.zeros((324, 64), object)},
            {"b2": np.array([np.nan, 0, 0, 0], np.float32)},
            {"mean": np.full((324,), np.inf, np.float32)},
            {"scale": np.full((324,), .01, np.float32)},
        ]
        for update in invalid:
            with self.subTest(key=list(update), shape=next(iter(update.values())).shape):
                self.write_model(updates=update)
                with self.assertLogs("BadgeModel", level="WARNING"):
                    self.assertIsNone(self.load())

    def test_manifest_schema_feature_policy_class_order_and_paths_are_fixed(self):
        updates = [
            lambda m: m.update(schema_version=True),
            lambda m: m.update(classes=["background", "Round", "Triangle", "Diamond"]),
            lambda m: m.update(feature_spec=dict(m["feature_spec"], signed_gradient=True)),
            lambda m: m.update(thresholds=dict(m["thresholds"], confidence=.10)),
            lambda m: m["weights"].update(file="../weights.npz"),
            lambda m: m["weights"].update(file="..\\weights.npz"),
            lambda m: m["weights"].update(file="C:weights.npz"),
        ]
        for update in updates:
            self.write_model(manifest_update=update)
            with self.assertLogs("BadgeModel", level="WARNING"):
                self.assertIsNone(self.load())

    def test_compressed_large_array_is_rejected_before_numpy_load(self):
        self.write_model(updates={"w1": np.zeros((1024, 1024), np.float32)})
        self.assertLess((self.folder / "weights.npz").stat().st_size, badge_model.MAX_MODEL_BYTES)
        with patch.object(badge_model.np, "load", side_effect=AssertionError("must not allocate")) as loading:
            with self.assertLogs("BadgeModel", level="WARNING"):
                self.assertIsNone(self.load())
            loading.assert_not_called()

    def test_loaded_memory_stays_frozen_without_runtime_file_reads(self):
        self.write_model()
        model = self.load()
        original = model.classify_badge(self.image, self.meta)
        (self.folder / "weights.npz").unlink()
        (self.folder / "badge-model.json").unlink()
        with patch.object(Path, "open", side_effect=AssertionError("frame must not read files")):
            self.assertEqual(model.classify_badge(self.image, self.meta), original)
        with self.assertRaises(ValueError):
            model._weights["w1"][0, 0] = 1
        with self.assertLogs("BadgeModel", level="WARNING"):
            self.assertIsNone(self.load())

    def test_finite_but_overflowing_weights_do_not_emit_invalid_probabilities(self):
        self.write_model(updates={"w1": np.full((324, 64), 1e38, np.float32),
                                  "b1": np.full((64,), 1e38, np.float32),
                                  "w2": np.full((64, 4), 1e38, np.float32)})
        result = self.load().classify_badge(self.image, self.meta)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "nonfinite_logits")


if __name__ == "__main__":
    unittest.main()
