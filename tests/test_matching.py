"""Synthetic matching tests. No screenshot calls or visible windows."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from mss.exception import ScreenShotError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.load_image import create_mask
from src.load_resolution import get_capture_area
from config import ROIS, FACE_OFFSET
from src.worker import MatcherWorker, _capture_gray, _match_score


class FakeCapture:
    def __init__(self, image):
        self.image = image
        self.area = None

    def grab(self, area):
        self.area = area
        return self.image


class MatchingTests(unittest.TestCase):
    def setUp(self):
        random = np.random.default_rng(42)
        self.template = random.integers(30, 220, size=(10, 10), dtype=np.uint8)
        self.image = random.integers(0, 255, size=(30, 40), dtype=np.uint8)
        self.image[12:22, 17:27] = self.template

    def test_exact_face_and_skill_match_in_larger_frame(self):
        diff = _match_score(self.image, self.template, cv2.TM_SQDIFF_NORMED, create_mask(10, 10))
        score = _match_score(self.image, self.template, cv2.TM_CCOEFF_NORMED)
        self.assertIsNotNone(diff)
        self.assertLess(diff, 1e-5)
        self.assertGreater(score, 0.999)

    def test_oversized_empty_and_constant_templates_are_ignored(self):
        self.assertIsNone(_match_score(self.image, np.ones((40, 50), np.uint8), cv2.TM_CCOEFF_NORMED))
        self.assertIsNone(_match_score(self.image, np.ones((5, 50), np.uint8), cv2.TM_CCOEFF_NORMED))
        self.assertIsNone(_match_score(self.image, np.ones((0, 0), np.uint8), cv2.TM_CCOEFF_NORMED))
        self.assertIsNone(_match_score(self.image, np.ones((10, 10), np.uint8), cv2.TM_CCOEFF_NORMED))

    def test_nonfinite_and_invalid_opencv_values_are_ignored(self):
        with patch("src.worker.cv2.matchTemplate", return_value=np.array([[np.nan, np.inf, -5.0, 0.2]])):
            self.assertAlmostEqual(_match_score(self.image, self.template, cv2.TM_SQDIFF_NORMED), 0.2)
        with patch("src.worker.cv2.matchTemplate", return_value=np.array([[np.nan, np.inf, -5.0]])):
            self.assertIsNone(_match_score(self.image, self.template, cv2.TM_SQDIFF_NORMED))
        with patch("src.worker.cv2.matchTemplate", return_value=np.array([[np.nan, 9.0, 0.8]])):
            self.assertAlmostEqual(_match_score(self.image, self.template, cv2.TM_CCOEFF_NORMED), 0.8)
        with patch("src.worker.cv2.matchTemplate", return_value=np.array([[-1e-7]])):
            self.assertEqual(_match_score(self.image, self.template, cv2.TM_SQDIFF_NORMED), 0.0)

    def test_negative_monitor_coordinates_and_small_resizing(self):
        capture = FakeCapture(np.zeros((2, 2, 4), np.uint8))
        area = {"left": -1200, "top": -500, "width": 2, "height": 2}
        gray = _capture_gray(capture, area, 0.1)
        self.assertEqual(gray.shape, (1, 1))
        self.assertEqual(capture.area["left"], -1200)
        self.assertIsNone(_capture_gray(capture, dict(area, height=0), 1.0))
        self.assertIsNone(_capture_gray(capture, area, float("nan")))

    def test_three_cards_match_their_own_skill_and_build_priority(self):
        random = np.random.default_rng(83)
        frame = random.integers(20, 220, size=(720, 1280), dtype=np.uint8)
        face = random.integers(20, 220, size=(15, 15), dtype=np.uint8)
        geo = {"x": 0, "y": 0, "w": 1280, "h": 720}
        skills = {}
        for index, roi in enumerate(ROIS):
            skill = random.integers(20, 220, size=(20, 20), dtype=np.uint8)
            skills[f"{index}.png"] = skill
            face_area = get_capture_area(geo, roi, FACE_OFFSET)
            card_area = get_capture_area(geo, roi)
            x, y = face_area["left"] + 3, face_area["top"] + 3
            frame[y:y + 15, x:x + 15] = face
            x, y = card_area["left"] + 30, card_area["top"] + 130
            frame[y:y + 20, x:x + 20] = skill

        class OfflineCapture:
            def grab(self, area):
                x, y = area["left"], area["top"]
                return frame[y:y + area["height"], x:x + area["width"]]

        class OfflineBuild:
            def get_priority(self, character, filename):
                assert character == "firenze"
                return {"0.png": 2, "1.png": 3, "2.png": 5}[filename]

            def get_target_level(self, character, filename):
                return None

        worker = MatcherWorker("unused-build.json")
        worker.build_loader = OfflineBuild()
        emitted = []
        worker.match_signal.connect(lambda *args: emitted.append(args))
        worker.process_rois(OfflineCapture(), geo,
                            {"firenze": (face, create_mask(15, 15))}, {"firenze": skills})
        self.assertEqual([row[1] for row in emitted], ["0.png", "1.png", "2.png"])
        self.assertEqual([row[4] for row in emitted], [2, 3, 5])
        self.assertTrue(all(row[3] and row[2] > 0.999 for row in emitted))

    def test_mss_failure_becomes_an_unmatched_capture(self):
        capture = FakeCapture(self.image)
        area = {"left": 0, "top": 0, "width": 40, "height": 30}
        with patch.object(capture, "grab", side_effect=ScreenShotError("window unavailable")):
            self.assertIsNone(_capture_gray(capture, area, 1.0))

    def test_native_party_never_identifies_a_nonparty_face_and_ties_are_unknown(self):
        worker = MatcherWorker("unused-build.json")

        class NativeParty:
            def get_recognition_characters(self):
                return frozenset({"allie", "donna", "suntide_willow"})

        worker.build_loader = NativeParty()
        pair = (self.template, create_mask(10, 10))
        geo = {"x": 0, "y": 0, "w": 1280, "h": 720}
        with patch("src.worker._capture_gray", return_value=self.image):
            name, _ = worker.detect_face(None, geo, ROIS[0], {"coronis": pair}, 1.0)
            self.assertIsNone(name)
            name, _ = worker.detect_face(None, geo, ROIS[0], {"allie": pair, "donna": pair}, 1.0)
            self.assertIsNone(name)

    def test_skill_capture_failure_clears_stale_priority(self):
        worker = MatcherWorker("unused-build.json")
        emitted = []
        worker.match_signal.connect(lambda *args: emitted.append(args))
        with patch("src.worker._capture_gray", return_value=None):
            worker.detect_skill(None, {"x": 0, "y": 0, "w": 1280, "h": 720},
                                {"x": 0, "y": 0, "w": 0.2, "h": 0.4},
                                "firenze", {"firenze": {"123.png": self.template}}, 1.0, 0)
        self.assertEqual(emitted, [(0, "", 0.0, False, 0, None)])

    def test_plain_bright_frame_cannot_be_a_face(self):
        worker = MatcherWorker("unused-build.json")
        # A nearly white portrait can pass SQDIFF's .15 threshold on solid white.
        face = np.full((10, 10), 225, np.uint8)
        face[3:7, 3:7] = 180
        for intensity in (0, 128, 255):
            image = np.full((40, 40), intensity, np.uint8)
            with patch("src.worker._capture_gray", return_value=image):
                name, _ = worker.detect_face(None, {"x": 0, "y": 0, "w": 1280, "h": 720},
                                            ROIS[0], {"allie": (face, create_mask(10, 10))}, 1.0)
            self.assertIsNone(name)


if __name__ == "__main__":
    unittest.main()
