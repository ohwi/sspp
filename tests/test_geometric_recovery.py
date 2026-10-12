"""Independent synthetic glyphs exercise alignment and fail-closed boundaries.

These fixtures are drawn here, without application renderers, source PIDs,
model outputs, saved game images, or expected coordinates entering inference.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.card_matcher import _match_card_fixed, match_card
from src.geometric_matcher import match_geometric_card


def draw_card(*, corner=2, kind=0, glyph_scale=1., shift=(0., 0.), hue=20):
    height, width = 170, 150
    hsv = np.empty((height, width, 3), np.uint8)
    yy, xx = np.mgrid[:height, :width]
    hsv[:, :, 0], hsv[:, :, 1] = hue, 200
    hsv[:, :, 2] = (100 + (xx * 3 + yy * 5) % 130).astype(np.uint8)
    image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    glyph = np.zeros((height, width), np.uint8)
    if kind == 0:
        cv2.circle(glyph, (70, 69), 18, 255, 5)
        cv2.line(glyph, (55, 92), (91, 48), 255, 5)
    else:
        cv2.rectangle(glyph, (55, 52), (89, 91), 255, 5)
        cv2.line(glyph, (53, 71), (96, 71), 255, 5)
    matrix = np.float32([[glyph_scale, 0, 75 * (1 - glyph_scale) + shift[0]],
                         [0, glyph_scale, 75 * (1 - glyph_scale) + shift[1]]])
    glyph = cv2.warpAffine(glyph, matrix, (width, height), flags=cv2.INTER_LINEAR)
    alpha = glyph[:, :, None] / 255.
    image = np.rint(image * (1 - alpha) + 255 * alpha).astype(np.uint8)
    if corner == 2:
        outer = [[75, 112], [95, 151], [55, 151]]
        inner = [[75, 120], [88, 146], [62, 146]]
    elif corner == 1:
        outer = [[75, 111], [96, 135], [75, 157], [54, 135]]
        inner = [[75, 120], [87, 135], [75, 148], [63, 135]]
    else:
        return image
    cv2.fillConvexPoly(image, np.array(outer, np.int32), (255, 255, 255))
    cv2.fillConvexPoly(image, np.array(inner, np.int32), (100, 70, 160))
    return image


def record(pid="123", *, corner=2, kind=0):
    image = draw_card(corner=corner, kind=kind)
    return {"filename": f"{pid}.png", "bgr": image,
            "gray": cv2.cvtColor(image, cv2.COLOR_BGR2GRAY),
            "meta": {"glyph": f"synthetic-{kind}", "corner": corner, "stype": 41,
                     "rarity": 2, "glyph_region": [0, 0, 150, 105],
                     "corner_region": [46, 106, 104, 164],
                     "layout": {"glyph": {"center": [75., 75.]}}}}


def pool(*records):
    return {row["filename"]: {key: value for key, value in row.items() if key != "filename"}
            for row in records}


def observed(image):
    frame = np.zeros((210, 195, 3), np.uint8)
    frame[20:190, 20:170] = image
    return frame


class GeometricEvidenceSafety(unittest.TestCase):
    def setUp(self):
        self.record = record()

    def test_glyph_scale_and_translation_recover_without_moving_card_or_badge(self):
        for scale, shift in ((.90, (-4, 3)), (.95, (3, -2)), (1.1, (3, -2))):
            with self.subTest(scale=scale, shift=shift):
                image = observed(draw_card(glyph_scale=scale, shift=shift))
                filename, score, diagnostic = match_geometric_card(image, [self.record], {"123"})
                self.assertEqual(filename, "123.png")
                self.assertGreaterEqual(score, .88)
                self.assertEqual(diagnostic["best"]["location"], [20, 20])
                self.assertEqual(diagnostic["best"]["card_size"], [150, 170])
                self.assertGreater(diagnostic["best"]["corner_score"], .99)

    def test_wrong_hue_unrelated_glyph_no_badge_and_blank_do_not_confirm(self):
        images = [observed(draw_card(hue=110)), observed(draw_card(kind=1)),
                  observed(draw_card(corner=0)), np.zeros((210, 195, 3), np.uint8),
                  np.full((210, 195, 3), 255, np.uint8)]
        for image in images:
            with self.subTest(mean=float(image.mean())):
                self.assertIsNone(match_geometric_card(image, [self.record], {"123"})[0])

    def test_observed_legal_badge_selects_its_candidate_and_keeps_common_anchor(self):
        diamond = record("124", corner=1)
        image = observed(draw_card(corner=1, glyph_scale=.95, shift=(3, -2)))
        filename, _, diagnostic = match_geometric_card(image, [self.record, diamond], {"123", "124"})
        self.assertEqual(filename, "124.png")
        self.assertEqual({tuple(row["location"]) for row in diagnostic["candidates"]}, {(20, 20)})

    def test_exact_pixel_alias_is_refused_and_role_filter_can_resolve_it(self):
        duplicate = {**self.record, "filename": "124.png"}
        image = observed(draw_card(glyph_scale=.95, shift=(3, -2)))
        filename, _, diagnostic = match_geometric_card(image, [self.record, duplicate], {"123", "124"})
        self.assertIsNone(filename)
        self.assertEqual(diagnostic["reason"], "identical_pixels")
        self.assertEqual(match_geometric_card(image, [self.record], {"123"})[0], "123.png")
        # A direct API caller may provide an unfiltered library; disallowed
        # duplicates must not become ambiguity evidence for the allowed role.
        self.assertEqual(match_geometric_card(image, [self.record, duplicate], {"123"})[0], "123.png")

    def test_near_failing_badge_is_still_conflicting_evidence(self):
        other = record("124", corner=1)
        glyph = {"score": .99, "scale": 1., "translation": [0., 0.]}
        expected = self.record["gray"][106:164, 46:104]

        def measured_component(actual, template):
            if actual.ndim == 1:
                return 1.
            return .46 if np.array_equal(template, expected) else .44

        with patch("src.geometric_matcher._glyph_score", return_value=glyph), \
                patch("src.geometric_matcher._ncc", side_effect=measured_component):
            filename, _, diagnostic = match_geometric_card(
                observed(draw_card()), [self.record, other], {"123", "124"})
        self.assertIsNone(filename)
        self.assertEqual(diagnostic["reason"], "insufficient_geometry_margin")

    def test_badge_pixels_are_removed_from_glyph_alignment_evidence(self):
        from src import geometric_matcher
        measured = []
        original = geometric_matcher._glyph_score

        def inspect_feature(feature, descriptor):
            measured.append(feature.copy())
            return original(feature, descriptor)

        with patch("src.geometric_matcher._glyph_score", side_effect=inspect_feature):
            filename, _, _ = match_geometric_card(
                observed(draw_card(glyph_scale=.95, shift=(3, -2))), [self.record], {"123"})
        self.assertEqual(filename, "123.png")
        self.assertTrue(measured)
        self.assertTrue(all(not np.any(feature[106:164, 46:104]) for feature in measured))

    def test_absent_or_empty_native_pool_and_gray_input_disable_geometry(self):
        image = observed(draw_card())
        for allowed in (None, set()):
            self.assertIsNone(match_geometric_card(image, [self.record], allowed)[0])
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        self.assertIsNone(match_geometric_card(gray, [self.record], {"123"})[0])

    def test_empty_and_invalid_channel_inputs_are_unknown_without_exceptions(self):
        invalid = [None, np.empty((0, 0, 3), np.uint8),
                   np.zeros((210, 195, 1), np.uint8), np.zeros((210, 195, 2), np.uint8),
                   np.zeros((210, 195, 5), np.uint8), np.zeros((210, 195, 3), np.float32)]
        for image in invalid:
            with self.subTest(shape=None if image is None else image.shape):
                self.assertIsNone(match_geometric_card(image, [self.record], {"123"})[0])

    def test_malformed_layout_does_not_crash_or_accept_geometry(self):
        bad = ["invalid", {"glyph": {}}, {"glyph": {"center": [float("nan"), 75]}},
               {"glyph": {"center": [75]}}, {"glyph": {"center": "75"}}]
        for layout in bad:
            row = copy.deepcopy(self.record)
            row["meta"]["layout"] = layout
            with self.subTest(layout=layout):
                self.assertIsNone(match_geometric_card(observed(draw_card()), [row], {"123"})[0])


class GeometryWorkerContracts(unittest.TestCase):
    def test_strong_existing_match_skips_geometric_work_and_preserves_score(self):
        row = record()
        image = observed(row["bgr"])
        expected = _match_card_fixed(image, "unit", pool(row), {"123"})
        with patch("src.geometric_matcher.match_geometric_card", side_effect=AssertionError("unused")):
            actual = match_card(image, "unit", pool(row), {"123"})
        self.assertEqual(actual[:2], expected[:2])
        self.assertEqual(actual[2]["reason"], expected[2]["reason"])

    def test_legacy_gray_matching_remains_available(self):
        row = record()
        image = cv2.cvtColor(observed(row["bgr"]), cv2.COLOR_BGR2GRAY)
        library = {"123.png": row["gray"]}
        with patch("src.geometric_matcher.match_geometric_card", side_effect=AssertionError("unused")):
            self.assertEqual(match_card(image, "unit", library, {"123"})[0], "123.png")

    def test_worker_preserves_zero_target_and_rejects_out_of_role_geometry(self):
        from config import ROIS
        from src.worker import MatcherWorker
        row = record()
        image = observed(draw_card(glyph_scale=.95, shift=(3, -2)))

        class SavedCapture:
            def grab(self, area):
                return image

        class NativeZero:
            def __init__(self, allowed):
                self.allowed = allowed

            def get_recognition_potentials(self, char):
                return self.allowed

            def get_priority(self, char, filename):
                return 0

            def get_target_level(self, char, filename):
                return 0

        worker = MatcherWorker("")
        worker.badge_model = None
        signals = []
        worker.match_signal.connect(lambda *args: signals.append(args))
        worker.build_loader = NativeZero({"123"})
        worker.detect_skill(SavedCapture(), {"x": 0, "y": 0, "w": 1280, "h": 720},
                            ROIS[0], "unit", {"unit": pool(row)}, 1., 0)
        self.assertEqual(signals[-1][1], "123.png")
        self.assertEqual(signals[-1][4:], (0, 0))
        worker.build_loader = NativeZero({"999"})
        worker.detect_skill(SavedCapture(), {"x": 0, "y": 0, "w": 1280, "h": 720},
                            ROIS[0], "unit", {"unit": pool(row)}, 1., 0)
        self.assertEqual(signals[-1][1], "unit (?)")
        self.assertIsNone(signals[-1][5])


if __name__ == "__main__":
    unittest.main()
