"""A geometric badge prediction cannot supply missing identity evidence."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.card_matcher import match_card
from src.geometric_matcher import match_geometric_card
from test_geometric_recovery import draw_card, observed, pool, record


class PredictedBadge:
    def __init__(self, corner=2, probabilities=None, valid=True):
        self.probabilities = ([.997 if index == corner else .001 for index in range(4)]
                              if probabilities is None else probabilities)
        self.valid = valid
        self.calls = []

    def classify_badge(self, patch, metadata):
        self.calls.append((patch.copy(), copy.deepcopy(metadata)))
        # Deliberately inconsistent reported class/margin: callers must derive
        # the accepted class and margin from the finite probability vector.
        return {"valid": self.valid, "corner": 3, "top_margin": 1.,
                "probs": self.probabilities, "reason": "unit_observation"}


class GeometricBadgeSafety(unittest.TestCase):
    def test_confident_badge_cannot_override_unrelated_glyph_wrong_color_or_background(self):
        row = record()
        noisy_background = draw_card(glyph_scale=.95, shift=(3, -2))
        hsv = np.empty(noisy_background.shape, np.uint8)
        hsv[:, :, 0], hsv[:, :, 1] = 20, 200
        hsv[:, :, 2] = np.random.default_rng(81).integers(90, 240, hsv.shape[:2], np.uint8)
        import cv2
        random_background = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        white = np.all(noisy_background > 240, axis=2)
        random_background[white] = noisy_background[white]
        for image in (observed(draw_card(kind=1)), observed(draw_card(hue=110)),
                      observed(random_background)):
            model = PredictedBadge()
            with self.subTest(mean=float(image.mean())):
                self.assertIsNone(match_geometric_card(image, [row], {"123"}, badge_model=model)[0])
                self.assertEqual(model.calls, [])

    def test_model_receives_only_observed_card_pixels_and_fixed_corner_region(self):
        row, model = record(), PredictedBadge()
        filename, _, diagnostic = match_geometric_card(
            observed(draw_card(glyph_scale=.95, shift=(3, -2))), [row], {"123"}, badge_model=model)
        self.assertEqual(filename, "123.png")
        self.assertTrue(model.calls)
        for patch_image, metadata in model.calls:
            self.assertEqual(patch_image.shape, (170, 150, 3))
            self.assertEqual(patch_image.dtype, np.uint8)
            self.assertEqual(metadata, {"corner_region": [46, 106, 104, 164]})
        self.assertEqual(diagnostic["best"]["badge_network"]["corner"], 2)

    def test_confident_badge_cannot_resolve_exact_pixels_or_near_tied_other_glyph(self):
        row, model = record(), PredictedBadge()
        duplicate = {**row, "filename": "124.png"}
        image = observed(draw_card(glyph_scale=.95, shift=(3, -2)))
        filename, _, evidence = match_geometric_card(image, [row, duplicate], {"123", "124"}, badge_model=model)
        self.assertIsNone(filename)
        self.assertEqual(evidence["reason"], "identical_pixels")
        near = copy.deepcopy(row)
        near["filename"], near["meta"]["glyph"] = "124.png", "different-source-near-pixels"
        near["bgr"][0, 0, 0] += 1
        import cv2
        near["gray"] = cv2.cvtColor(near["bgr"], cv2.COLOR_BGR2GRAY)
        filename, _, evidence = match_geometric_card(image, [row, near], {"123", "124"}, badge_model=PredictedBadge())
        self.assertIsNone(filename)
        self.assertEqual(evidence["reason"], "insufficient_geometry_margin")

    def test_invalid_probabilities_do_not_override_a_mismatching_observed_badge(self):
        row = record()
        image = observed(draw_card(corner=1))
        invalid = [[.25] * 4, [0., 0., .94, .06], [0., 0., float("nan"), 1.],
                   [0., 0., 1.], [0., 0., 2., 0.], [.96, 0., .04, 0.]]
        for probabilities in invalid:
            with self.subTest(probabilities=probabilities):
                self.assertIsNone(match_geometric_card(
                    image, [row], {"123"}, badge_model=PredictedBadge(probabilities=probabilities))[0])
        self.assertIsNone(match_geometric_card(
            image, [row], {"123"}, badge_model=PredictedBadge(valid=False))[0])


class WeakFixedBadgeContracts(unittest.TestCase):
    def test_weak_primary_can_be_corrected_using_all_ranked_observations(self):
        triangle, diamond = record(), record("124", corner=1)
        rows = [triangle]
        for pid in range(200, 205):
            distractor = copy.deepcopy(triangle)
            distractor["filename"] = f"{pid}.png"
            distractor["meta"]["glyph"] = f"distractor-{pid}"
            distractor["bgr"][0, 0, 0] += pid - 199
            import cv2
            distractor["gray"] = cv2.cvtColor(distractor["bgr"], cv2.COLOR_BGR2GRAY)
            rows.append(distractor)
        rows.append(diamond)
        ranked = [{"filename": row["filename"], "glyph_score": .86, "full_score": .80,
                   "hue_distance": 0., "location": [20, 20],
                   "family": f"{row['meta']['glyph']}:41:2"} for row in rows]
        evidence = {"accepted": True, "reason": "accepted", "candidates": ranked[:5],
                    "ranked_candidates": ranked}
        model = PredictedBadge(corner=1)
        with patch("src.card_matcher._match_card_fixed", return_value=("123.png", .80, evidence)), \
                patch("src.geometric_matcher.match_geometric_card", side_effect=AssertionError("unused")):
            filename, score, diagnostic = match_card(
                observed(draw_card(corner=1)), "unit", pool(*rows),
                {row["filename"].removesuffix(".png") for row in rows}, badge_model=model)
        self.assertEqual(filename, "124.png")
        self.assertEqual(score, .86)
        self.assertEqual(diagnostic["reason"], "accepted_badge_network")
        self.assertEqual(diagnostic["previous_filename"], "123.png")
        self.assertTrue(all(set(metadata) == {"corner_region"} for _, metadata in model.calls))

    def test_existing_network_acceptance_and_mixed_legacy_acceptance_keep_their_original_tuple(self):
        row = record()
        image = observed(draw_card(glyph_scale=.95, shift=(3, -2)))
        library = pool(row)
        library["987.png"] = row["gray"]
        for filename, reason in (("123.png", "accepted_badge_network"), ("987.png", "accepted")):
            expected = (filename, .82, {"accepted": True, "reason": reason, "candidates": []})
            with self.subTest(filename=filename), \
                    patch("src.card_matcher._match_card_fixed", return_value=expected), \
                    patch("src.card_matcher._recover_badge", side_effect=AssertionError("unused")), \
                    patch("src.geometric_matcher.match_geometric_card", side_effect=AssertionError("unused")):
                actual = match_card(image, "unit", library, {"123", "987"}, badge_model=PredictedBadge())
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
