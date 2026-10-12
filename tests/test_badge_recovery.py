"""An overconfident badge model cannot bypass native visual evidence."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.card_matcher import match_card


class CertainBadge:
    def __init__(self, probs=None):
        self.probs = probs if probs is not None else [.001, .001, .997, .001]
        self.calls = []

    def classify_badge(self, patch, meta):
        self.calls.append((patch.shape, meta))
        return {"valid": True, "corner": 2, "probs": self.probs,
                "top_margin": .996, "reason": "test_observer"}


class BadgeRecoverySafety(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(612)
        hsv = np.zeros((70, 60, 3), np.uint8)
        hsv[:, :, 0] = 20
        hsv[:, :, 1] = 200
        hsv[:, :, 2] = rng.integers(90, 240, (70, 60), np.uint8)
        color = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        self.record = {"gray": cv2.cvtColor(color, cv2.COLOR_BGR2GRAY), "bgr": color,
                       "meta": {"glyph": "public_glyph", "corner": 2, "stype": 41, "rarity": 2,
                                "glyph_region": [0, 0, 60, 40], "corner_region": [15, 40, 45, 70]}}
        observed = hsv.copy()
        observed[40:70, 15:45, 2] = rng.integers(90, 240, (30, 30), np.uint8)
        self.image = cv2.cvtColor(observed, cv2.COLOR_HSV2BGR)
        self.pool = {"123.png": self.record}

    def recover(self, image=None, pool=None, allowed=None, model=None):
        return match_card(self.image if image is None else image, "unit", self.pool if pool is None else pool,
                          allowed_ids={"123"} if allowed is None else allowed,
                          badge_model=CertainBadge() if model is None else model)

    def test_independent_badge_can_recover_a_refused_corner_without_receiving_expected_class(self):
        baseline = match_card(self.image, "unit", self.pool, {"123"})
        self.assertIsNone(baseline[0])
        model = CertainBadge()
        filename, score, diagnostic = self.recover(model=model)
        self.assertEqual(filename, "123.png")
        self.assertGreater(score, .99)
        self.assertEqual(diagnostic["reason"], "accepted_badge_network")
        self.assertEqual(diagnostic["score_basis"], "glyph_ncc")
        self.assertTrue(model.calls)
        self.assertTrue(all(set(meta) == {"corner_region"} for _, meta in model.calls))

    def test_role_missing_or_empty_does_not_enable_network_recovery(self):
        for allowed in (None, set()):
            model = CertainBadge()
            filename, _, _ = match_card(self.image, "unit", self.pool, allowed, badge_model=model)
            self.assertIsNone(filename)
            self.assertEqual(model.calls, [])

    def test_high_badge_confidence_does_not_override_wrong_hue_or_absent_color(self):
        hsv = cv2.cvtColor(self.image, cv2.COLOR_BGR2HSV)
        hsv[:, :, 0] = 110
        wrong = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        gray = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
        for image in (wrong, gray, np.repeat(gray[:, :, None], 3, axis=2)):
            self.assertIsNone(self.recover(image=image)[0])

    def test_high_badge_confidence_does_not_override_unrelated_glyph_or_blank_frame(self):
        unrelated = np.random.default_rng(613).integers(0, 255, self.image.shape, np.uint8)
        for image in (unrelated, np.zeros_like(self.image), np.full_like(self.image, 255)):
            self.assertIsNone(self.recover(image=image)[0])

    def test_exact_duplicate_ids_remain_unresolved_with_a_network(self):
        pool = {"123.png": self.record, "124.png": self.record}
        self.assertIsNone(self.recover(pool=pool, allowed={"123", "124"})[0])

    def test_unreliable_or_invalid_network_outputs_do_not_force_a_card(self):
        invalid = [[.25] * 4, [0, 0, .6, .4], [0, 0, float("nan"), 0],
                   [0, 0, 1], [0, 0, 2, 0], [0, 0, .96, .96]]
        for probabilities in invalid:
            with self.subTest(probabilities=probabilities):
                self.assertIsNone(self.recover(model=CertainBadge(probabilities))[0])

    def test_accepted_existing_match_is_not_changed_by_a_conflicting_network(self):
        model = CertainBadge([.001, .997, .001, .001])
        filename, _, diagnostic = self.recover(image=self.record["bgr"], model=model)
        self.assertEqual(filename, "123.png")
        self.assertEqual(diagnostic["reason"], "accepted")
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
