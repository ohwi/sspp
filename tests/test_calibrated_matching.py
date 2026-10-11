"""Independent benchmark contracts and native preset path; no live capture."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calibrated_benchmark", REPO / "scripts/benchmark_calibrated_matching.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkContracts(unittest.TestCase):
    def test_split_is_chronological_even_odd_and_order_independent(self):
        ids = ["050552", "050611", "050630", "050720", "050731", "050800", "050814", "050832",
               "050845", "050850", "050857", "050906", "050912", "050918", "050923", "050942"]
        actual = benchmark.fixed_split([{"id": value} for value in reversed(ids)])
        self.assertEqual([key for key, fold in actual.items() if fold == "fit"], ids[::2])
        self.assertEqual([key for key, fold in actual.items() if fold == "test"], ids[1::2])
        with self.assertRaises(ValueError):
            benchmark.fixed_split([{"id": "050552"}] * 16)

    def test_geometry_retains_real_border_origin_and_first_image_dimensions(self):
        self.assertEqual(benchmark.client_geometry({"width": 1602, "height": 932}), {"x": 1, "y": 31, "w": 1600, "h": 900})
        self.assertEqual(benchmark.client_geometry({"width": 1594, "height": 897}), {"x": 0, "y": 0, "w": 1594, "h": 897})

    def test_accepted_wrong_pid_and_character_only_are_not_correct(self):
        cards = [{"index": 0, "character_key": "allie", "potential_id": "513910", "name_ko": "label"},
                 {"index": 1, "character_key": "allie", "potential_id": "513901", "name_ko": "label"},
                 {"index": 2, "character_key": "allie", "potential_id": "513913", "name_ko": "label"}]
        decision = {"match_signals": [
            {"index": 0, "text": "513904.png", "score": .99, "matched": True, "priority": 0, "target_level": 0},
            {"index": 1, "text": "allie (?)", "score": 0, "matched": True, "priority": 0, "target_level": None},
            {"index": 2, "text": "513913.png", "score": .88, "matched": True, "priority": 2, "target_level": 6}],
            "debug_signals": [{"index": i, "text": "[FACE]allie", "score": .95} for i in range(3)]}
        results = benchmark.evaluate_decisions(decision, cards)
        summary = benchmark.summarize(results)
        self.assertEqual((summary["potential_correct"], summary["potential_wrong"], summary["potential_unknown"]), (1, 1, 1))
        self.assertEqual(summary["face_correct"], 3)
        self.assertEqual(results[0]["target_level"], 0)

    def test_rejected_face_debug_candidate_and_diagnostic_face_are_not_accuracy(self):
        cards = [{"index": 0, "character_key": "allie", "potential_id": "513913", "name_ko": "label"}]
        decision = {"match_signals": [{"index": 0, "text": "", "score": 0, "matched": False, "priority": 0, "target_level": None}],
                    "debug_signals": [{"index": 0, "text": "[FACE]allie", "score": .99}]}
        self.assertEqual(benchmark.evaluate_decisions(decision, cards)[0]["face_status"], "unknown")
        diagnostic = benchmark.summarize(benchmark.evaluate_decisions(decision, cards, face_stage=False))
        self.assertIsNone(diagnostic["face_correct"])
        self.assertEqual(diagnostic["potential_unknown"], 1)

    def test_union_overwrites_same_character_pid_without_losing_others(self):
        merged = benchmark.merge_skills({"allie": {"1.png": "old"}, "amber": {"2.png": "keep"}}, {"allie": {"1.png": "new", "3.png": "add"}})
        self.assertEqual(merged, {"allie": {"1.png": "new", "3.png": "add"}, "amber": {"2.png": "keep"}})

    def test_real_native_import_and_buildloader_preserve_all_roles_and_zero(self):
        from src.load_build import BuildLoader
        if not hasattr(BuildLoader, "get_recognition_potentials"):
            self.skipTest("Calibrated worker integration is not present yet")
        with tempfile.TemporaryDirectory(prefix="sspp-calibrated-native-test-") as directory:
            _, loader, receipt = benchmark.prepare_native_build(Path(directory))
            self.assertEqual(receipt["exact_target_count"], 48)
            self.assertGreater(receipt["zero_target_count"], 0)
            self.assertEqual([(row["position"], row["key"]) for row in receipt["slots"]],
                             [("master", "allie"), ("assist1", "suntide_willow"), ("assist2", "donna")])
            self.assertEqual(loader.get_target_level("allie", "513901.png"), 0)
            self.assertEqual(loader.get_target_level("suntide_willow", "516033.png"), 0)
            self.assertEqual(loader.get_target_level("donna", "513030.png"), 2)
            self.assertIn("513901", set(map(str, loader.get_recognition_potentials("allie"))))
            self.assertNotIn("513933", set(map(str, loader.get_recognition_potentials("allie"))))


class CalibratedMatcherBehavior(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        from src.card_matcher import match_card
        self.cv2, self.np, self.match_card = cv2, np, match_card
        random = np.random.default_rng(1942)
        self.template = random.integers(15, 240, (30, 30), dtype=np.uint8)
        self.image = random.integers(15, 240, (90, 90), dtype=np.uint8)
        self.image[25:55, 32:62] = self.template

    def test_exact_duplicate_is_unknown_without_role_and_resolves_single_allowed_pid(self):
        library = {"allie": {"513913.png": self.template, "513933.png": self.template.copy()}}
        filename, score, diagnostic = self.match_card(self.image, "allie", library)
        self.assertIsNone(filename)
        self.assertGreater(score, .99)
        self.assertFalse(diagnostic["accepted"])
        self.assertEqual(diagnostic["reason"], "identical_pixels")
        self.assertEqual(set(diagnostic["ambiguous_ids"]), {"513913.png", "513933.png"})
        for allowed in ({"513913"}, {"513933.png"}):
            filename, score, diagnostic = self.match_card(self.image, "allie", library, allowed_ids=allowed)
            self.assertEqual(filename, next(iter(allowed)).removesuffix(".png") + ".png")
            self.assertTrue(diagnostic["accepted"])
            self.assertGreater(score, .99)

    def test_empty_allowed_pool_is_unknown_even_for_an_exact_image(self):
        result = self.match_card(self.image, "allie", {"allie": {"513913.png": self.template}}, allowed_ids=set())
        self.assertIsNone(result[0])
        self.assertFalse(result[2]["accepted"])
        self.assertEqual(result[2]["reason"], "empty_candidate_pool")

    def test_distinct_pixels_with_a_negligible_runner_up_margin_are_unknown(self):
        runner_up = self.template.copy()
        runner_up[0, 0] += 1
        self.assertFalse(self.np.array_equal(self.template, runner_up))
        filename, _, diagnostic = self.match_card(self.image, "allie", {
            "513913.png": self.template, "513933.png": runner_up})
        self.assertIsNone(filename)
        self.assertFalse(diagnostic["accepted"])
        self.assertEqual(diagnostic["reason"], "insufficient_margin")

    def test_enriched_record_requires_observed_color_instead_of_silent_gray_fallback(self):
        random = self.np.random.default_rng(1943)
        color = random.integers(15, 240, (30, 30, 3), dtype=self.np.uint8)
        gray = self.cv2.cvtColor(color, self.cv2.COLOR_BGR2GRAY)
        image = self.np.tile(gray, (3, 3))
        enriched = {"gray": gray, "bgr": color,
                    "meta": {"glyph": "unique_public_glyph", "corner": 0, "stype": 42, "rarity": 1,
                             "glyph_region": [0, 0, 30, 20], "corner_region": [0, 20, 30, 30]}}
        accepted, _, positive = self.match_card(self.np.tile(color, (3, 3, 1)), "allie", {"allie": {"513913.png": enriched}})
        self.assertEqual(accepted, "513913.png")
        self.assertTrue(positive["accepted"])
        filename, _, diagnostic = self.match_card(image, "allie", {"allie": {"513913.png": enriched}})
        self.assertIsNone(filename)
        self.assertFalse(diagnostic["accepted"])
        self.assertEqual(diagnostic["reason"], "missing_color")

    def _colored_record(self, hue=20, height=30):
        random = self.np.random.default_rng(1945)
        hsv = self.np.empty((height, 30, 3), dtype=self.np.uint8)
        hsv[:, :, 0] = hue
        hsv[:, :, 1] = 190
        hsv[:, :, 2] = random.integers(80, 240, (height, 30), dtype=self.np.uint8)
        color = self.cv2.cvtColor(hsv, self.cv2.COLOR_HSV2BGR)
        gray = self.cv2.cvtColor(color, self.cv2.COLOR_BGR2GRAY)
        return {"gray": gray, "bgr": color,
                "meta": {"glyph": "same_public_glyph", "corner": 0, "stype": 42, "rarity": 1,
                         "glyph_region": [0, 0, 30, 20], "corner_region": [0, 20, 30, height]}}

    def test_desaturated_color_is_unknown_despite_an_exact_gray_match(self):
        record = self._colored_record()
        # All three channels are equal, so the observed gray image is exact but
        # contains no evidence of the source card's background hue.
        desaturated = self.np.repeat(record["gray"][:, :, None], 3, axis=2)
        filename, score, diagnostic = self.match_card(self.np.tile(desaturated, (3, 3, 1)), "allie", {"513913.png": record})
        self.assertIsNone(filename)
        self.assertGreater(score, .99)
        self.assertFalse(diagnostic["accepted"])
        self.assertTrue(diagnostic["color_checked"])
        self.assertEqual(diagnostic["reason"], "component_mismatch")

    def test_different_hue_is_unknown_despite_a_high_gray_correlation(self):
        expected = self._colored_record(hue=20)
        observed = self._colored_record(hue=110)
        filename, score, diagnostic = self.match_card(self.np.tile(observed["bgr"], (3, 3, 1)), "allie", {"513913.png": expected}, allowed_ids={"513913"})
        self.assertIsNone(filename)
        self.assertGreater(score, .99)
        self.assertFalse(diagnostic["accepted"])
        self.assertEqual(diagnostic["reason"], "component_mismatch")

    def test_non_square_enriched_template_is_not_assumed_to_be_150_square(self):
        record = self._colored_record(height=34)
        filename, score, diagnostic = self.match_card(self.np.tile(record["bgr"], (3, 3, 1)), "allie", {"513913.png": record})
        self.assertEqual(filename, "513913.png")
        self.assertGreater(score, .99)
        self.assertTrue(diagnostic["accepted"])

    def test_invalid_oversized_empty_and_constant_candidates_do_not_match(self):
        candidates = [self.np.empty((0, 0), self.np.uint8),
                      self.np.ones((30, 30), self.np.uint8),
                      self.np.tile(self.template, (4, 4)),
                      self.template.astype(self.np.float32)]
        for candidate in candidates:
            with self.subTest(shape=candidate.shape, dtype=str(candidate.dtype)):
                filename, _, diagnostic = self.match_card(self.image, "allie", {"513913.png": candidate})
                self.assertIsNone(filename)
                self.assertFalse(diagnostic["accepted"])

    def test_blank_observations_and_invalid_images_are_unknown(self):
        for observation in [None, self.np.empty((0, 0), self.np.uint8),
                            self.image.astype(self.np.float32),
                            self.np.zeros((90, 90), self.np.uint8),
                            self.np.full((90, 90), 128, self.np.uint8),
                            self.np.full((90, 90), 255, self.np.uint8)]:
            filename, _, diagnostic = self.match_card(observation, "allie", {"513913.png": self.template})
            self.assertIsNone(filename)
            self.assertFalse(diagnostic["accepted"])

    def test_nonfinite_opencv_results_are_unknown(self):
        with patch("src.card_matcher.cv2.matchTemplate", return_value=self.np.array([[self.np.nan, self.np.inf, -self.np.inf]])):
            filename, _, diagnostic = self.match_card(self.image, "allie", {"513913.png": self.template})
        self.assertIsNone(filename)
        self.assertFalse(diagnostic["accepted"])

    def test_blank_face_frame_does_not_confirm_the_nearest_bright_portrait(self):
        from src.load_image import create_mask
        from src.worker import MatcherWorker
        random = self.np.random.default_rng(1944)
        bright_face = random.integers(220, 251, (65, 65), dtype=self.np.uint8)

        class FlatCapture:
            def grab(_, area):
                return self.np.full((area["height"], area["width"], 4), 255, self.np.uint8)

        worker = MatcherWorker("")
        emitted = []
        worker.match_signal.connect(lambda *args: emitted.append(args))
        worker.process_rois(FlatCapture(), {"x": 0, "y": 0, "w": 1600, "h": 900},
                            {"allie": (bright_face, create_mask(65, 65))}, {})
        self.assertEqual(len(emitted), 3)
        self.assertTrue(all(not row[3] and row[1] == "" and row[5] is None for row in emitted))


if __name__ == "__main__":
    unittest.main()
