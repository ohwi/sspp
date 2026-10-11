"""Offscreen overlay rendering; no native overlay is shown."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtCore import Qt
from src.overlay import OverlayWindow
from src.load_resolution import get_capture_area
from src.worker import MatcherWorker


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_overlay_geometry_follows_client_on_negative_origin_monitor(self):
        with patch.object(OverlayWindow, "_init_window_settings"):
            overlay = OverlayWindow()
        self.addCleanup(overlay.deleteLater)
        geo = {"x": -1500, "y": -400, "w": 1280, "h": 720}
        overlay.update_geometry(geo)
        rect = overlay.geometry()
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()), (-1500, -400, 1280, 720))
        geo["x"] = 100
        self.assertEqual(overlay.cached_geo["x"], -1500)

    def test_native_signal_metadata_renders_neutrally_and_legacy_calls_still_work(self):
        with patch.object(OverlayWindow, "_init_window_settings"):
            overlay = OverlayWindow()
        self.addCleanup(overlay.deleteLater)
        worker = MatcherWorker("unused-build.json")
        worker.match_signal.connect(overlay.update_result)
        geo = {"x": 0, "y": 0, "w": 1280, "h": 720}
        roi = {"x": 0.2, "y": 0.2, "w": 0.2, "h": 0.4}
        for level in (2, 3, 0):
            worker.match_signal.emit(0, "510305.png", 0.99, True, 2 if level else 0, level)
            self.assertEqual(overlay.matches[0][3], level)
            image = QImage(1280, 720, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            overlay._draw_roi_visuals(painter, 0, roi, geo)
            painter.end()
            area = get_capture_area(geo, roi)
            color = image.pixelColor(area["left"], area["top"] + area["height"] // 2)
            self.assertEqual(color, OverlayWindow.match_style(2 if level else 0, level)[0])
            self.assertNotEqual(color, OverlayWindow.PRIORITY_STYLES[2][0])
        overlay.update_result(0, "legacy.png", 0.9, True, 5)
        self.assertIsNone(overlay.matches[0][3])
        worker.match_signal.emit(0, "", 0.0, False, 0, None)
        self.assertNotIn(0, overlay.matches)

    def test_priority_border_renders_at_client_local_coordinates(self):
        with patch.object(OverlayWindow, "_init_window_settings"):
            overlay = OverlayWindow()
        self.addCleanup(overlay.deleteLater)
        geo = {"x": -1500, "y": -400, "w": 1280, "h": 720}
        roi = {"x": 0.2, "y": 0.2, "w": 0.2, "h": 0.4}
        image = QImage(geo["w"], geo["h"], QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.translate(-geo["x"], -geo["y"])
        overlay._draw_match_result(painter, roi, geo, 5)
        painter.end()
        area = get_capture_area(geo, roi)
        x = area["left"] - geo["x"]
        y = area["top"] - geo["y"] + area["height"] // 2
        self.assertEqual(image.pixelColor(x, y), OverlayWindow.PRIORITY_STYLES[5][0])
        self.assertEqual(image.pixelColor(x + 15, y).alpha(), 0)


if __name__ == "__main__":
    unittest.main()
