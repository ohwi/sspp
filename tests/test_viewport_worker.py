"""Worker viewport policy checks without a thread, GUI, game or live capture."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src import worker as worker_module
from src.worker import MatcherWorker, _capture_gray, _capture_image


class SavedCapture:
    def __init__(self, image):
        self.image = image
        self.areas = []

    def grab(self, area):
        self.areas.append(dict(area))
        return self.image


class ViewportWorkerPolicy(unittest.TestCase):
    def setUp(self):
        with patch("src.worker.load_badge_model", return_value=None):
            self.worker = MatcherWorker("")
        self.geometry_signals, self.matches, self.debug = [], [], []
        self.worker.geometry_signal.connect(lambda geometry: self.geometry_signals.append(dict(geometry)))
        self.worker.match_signal.connect(lambda *args: self.matches.append(args))
        self.worker.debug_signal.connect(lambda *args: self.debug.append(args))
        self.worker.detect_skill = Mock()
        self.face_templates = {"allie": "fixture", "donna": "fixture"}
        self.skills = {"allie": {}, "donna": {}}
        self.geo = {"x": -1700, "y": 230, "w": 1600, "h": 1000}

    def run_aspect_case(self, normal, alternate):
        self.worker.detect_face = Mock(side_effect=lambda sct, geo, roi, faces, scale:
                                       alternate if isinstance(scale, tuple) else normal)
        capture = SavedCapture(np.full((1000, 1600, 3), 80, np.uint8))
        self.worker.process_rois(capture, self.geo, self.face_templates, self.skills)
        return capture

    def test_observed_letterbox_uses_identical_content_for_overlay_faces_and_cards(self):
        client = np.zeros((1000, 1600, 4), np.uint8)
        client[:, :, 3] = 255
        client[50:950, :, :3] = (30, 70, 170)
        capture = SavedCapture(client)
        self.worker.detect_face = Mock(return_value=("allie", .1))
        self.worker.process_rois(capture, self.geo, self.face_templates, self.skills)
        content = {"x": -1700, "y": 280, "w": 1600, "h": 900}
        self.assertEqual(self.geometry_signals, [content])
        self.assertEqual(capture.areas, [{"left": -1700, "top": 230, "width": 1600, "height": 1000}])
        self.assertEqual(self.worker.detect_face.call_count, 3)
        self.assertEqual(self.worker.detect_skill.call_count, 3)
        for call in self.worker.detect_face.call_args_list:
            self.assertEqual(call.args[1], content)
            self.assertEqual(call.args[-1], .8)
        for call in self.worker.detect_skill.call_args_list:
            self.assertEqual(call.args[1], content)
            self.assertEqual(call.args[-2], .8)

    def test_reference_aspect_keeps_the_original_path_and_needs_no_full_client_capture(self):
        geometry = {"x": -123, "y": 91, "w": 1600, "h": 900}
        capture = SavedCapture(None)
        self.worker.detect_face = Mock(return_value=("allie", .1))
        self.worker.process_rois(capture, geometry, self.face_templates, self.skills)
        self.assertEqual(capture.areas, [])
        self.assertEqual(self.geometry_signals, [geometry])
        self.assertEqual(self.worker.detect_face.call_count, 3)
        self.assertTrue(all(call.args[-2] == .8 for call in self.worker.detect_skill.call_args_list))

    def test_same_geometry_observation_is_cached_for_one_second_then_refreshed(self):
        capture = SavedCapture(np.full((1000, 1600, 3), 80, np.uint8))
        self.worker.detect_face = Mock(return_value=(None, 1.))
        with patch("src.worker.time.monotonic", side_effect=[10., 10.8, 11.001]):
            for _ in range(3):
                self.worker.process_rois(capture, dict(self.geo), self.face_templates, self.skills)
        self.assertEqual(len(capture.areas), 2)
        self.assertEqual(self.worker._content_geometry_cache[0], (-1700, 230, 1600, 1000))
        self.assertEqual(self.worker._content_geometry_cache[1], 11.001)
        self.assertEqual(self.geometry_signals, [self.geo] * 3)

    def test_any_position_or_size_change_invalidates_the_cached_viewport(self):
        self.worker.detect_face = Mock(return_value=(None, 1.))
        snapshots = []
        for changed in ("x", "y", "w", "h"):
            geometry = dict(self.geo)
            geometry[changed] += 1
            snapshots.append(geometry)
        geometries = [self.geo, *snapshots]
        capture = SavedCapture(np.full((1000, 1600, 3), 80, np.uint8))
        with patch("src.worker.time.monotonic", side_effect=[20. + i * .1 for i in range(5)]):
            for geometry in geometries:
                self.worker.process_rois(capture, geometry, self.face_templates, self.skills)
        self.assertEqual(len(capture.areas), 5)
        self.assertEqual(self.geometry_signals, geometries)
        self.assertEqual([(area["left"], area["top"], area["width"], area["height"])
                          for area in capture.areas],
                         [(geo["x"], geo["y"], geo["w"], geo["h"]) for geo in geometries])

    def test_alternate_normalization_requires_a_passed_face_and_clear_improvement(self):
        self.run_aspect_case(("allie", .23), ("allie", .10))
        self.assertEqual(self.geometry_signals, [self.geo])
        self.assertEqual(self.worker.detect_face.call_count, 6)
        self.assertTrue(all(call.args[-2] == (.8, .72) for call in self.worker.detect_skill.call_args_list))
        self.assertTrue(all(row[1] == "[FACE]allie" for row in self.debug))

    def test_small_improvement_keeps_the_original_scale(self):
        self.run_aspect_case(("allie", .13), ("allie", .115))
        self.assertEqual(self.worker.detect_skill.call_count, 3)
        self.assertTrue(all(call.args[-2] == .8 for call in self.worker.detect_skill.call_args_list))

    def test_better_but_failed_alternate_face_does_not_create_a_match(self):
        self.run_aspect_case(("allie", .30), ("allie", .16))
        self.worker.detect_skill.assert_not_called()
        self.assertEqual(len(self.matches), 3)
        self.assertTrue(all(not row[3] and row[1] == "" and row[5] is None for row in self.matches))

    def test_close_disagreement_between_valid_face_normalizations_is_unknown(self):
        self.run_aspect_case(("allie", .10), ("donna", .11))
        self.worker.detect_skill.assert_not_called()
        self.assertEqual(len(self.matches), 3)
        self.assertTrue(all(not row[3] and row[1] == "" and row[5] is None for row in self.matches))
        self.assertTrue(all(row[1] == "No Face" for row in self.debug))

    def test_clearly_better_alternate_face_selects_its_own_character_and_scale(self):
        self.run_aspect_case(("allie", .14), ("donna", .10))
        self.assertEqual(self.worker.detect_skill.call_count, 3)
        self.assertTrue(all(call.args[3] == "donna" and call.args[-2] == (.8, .72)
                            for call in self.worker.detect_skill.call_args_list))


class CaptureNormalizationInputSafety(unittest.TestCase):
    def setUp(self):
        self.area = {"left": -20, "top": 30, "width": 10, "height": 20}
        self.image = np.arange(600, dtype=np.uint8).reshape(20, 10, 3)

    def test_two_axis_capture_changes_only_the_observed_pixel_dimensions(self):
        capture = SavedCapture(self.image)
        image = _capture_image(capture, self.area, (.5, 2.))
        self.assertEqual(image.shape, (40, 5, 3))
        self.assertEqual(image.dtype, np.uint8)
        self.assertEqual(capture.areas, [self.area])
        gray = _capture_gray(SavedCapture(self.image), self.area, (.5, 2.))
        self.assertEqual(gray.shape, (40, 5))
        self.assertEqual(gray.dtype, np.uint8)

    def test_invalid_scale_types_and_nonfinite_axes_are_rejected_without_capture(self):
        for scale in (None, "bad", True, False, (.8, "bad"), (True, .8),
                      (.8,), (.8, .8, .8), (.8, float("nan")), (float("inf"), .8)):
            capture = SavedCapture(self.image)
            with self.subTest(scale=scale):
                self.assertIsNone(_capture_image(capture, self.area, scale))
                self.assertEqual(capture.areas, [])

    def test_malformed_capture_arrays_fail_closed_before_face_variance(self):
        bad = [np.ones((20, 10), dtype=bool), np.ones((20, 10, 3), dtype=bool),
               self.image.astype(np.float32), np.zeros((20, 10, 2), np.uint8),
               np.zeros((20, 10, 5), np.uint8), np.empty((0, 10, 3), np.uint8)]
        for image in bad:
            with self.subTest(shape=image.shape, dtype=str(image.dtype)):
                capture = SavedCapture(image)
                self.assertIsNone(_capture_image(capture, self.area, 1.))
                self.assertIsNone(_capture_gray(capture, self.area, 1.))

    def test_malformed_card_records_and_boolean_metadata_are_not_geometry_candidates(self):
        from src.geometric_matcher import match_geometric_card
        gray = np.ones((170, 150), np.uint8)
        color = np.repeat(gray[:, :, None], 3, axis=2)
        meta = {"glyph": "synthetic", "corner": 2, "stype": 41, "rarity": 2,
                "glyph_region": [0, 0, 150, 105], "corner_region": [46, 106, 104, 164],
                "layout": {"glyph": {"center": [75., 75.]}}}
        base = {"filename": "123.png", "gray": gray, "bgr": color, "meta": meta}
        invalid = [None, [], "invalid", {}, {**base, "filename": "invalid.png"},
                   {**base, "gray": gray.astype(bool)}, {**base, "bgr": None},
                   {**base, "meta": {**meta, "corner": True}},
                   {**base, "meta": {**meta, "rarity": False}},
                   {**base, "meta": {**meta, "glyph_region": [False, 0, 150, 105]}}]
        for row in invalid:
            with self.subTest(record_type=type(row).__name__):
                filename, score, diagnostic = match_geometric_card(
                    np.zeros((210, 195, 3), np.uint8), [row], {"123"})
                self.assertIsNone(filename)
                self.assertEqual(score, 0.)
                self.assertFalse(diagnostic["accepted"])


if __name__ == "__main__":
    unittest.main()
