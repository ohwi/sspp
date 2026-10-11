"""Test the native code entry without desktop input or worker threads."""
import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QSignalSpy
from src.load_build import BuildLoader
from src.sstoy_loader import build_maker

CODE = "AAAAiwAAAKAAAACCMAbDZDDNlgAABs2AQDwA"


class PresetImportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def window(self):
        window = build_maker.BuildMaker()
        self.addCleanup(window.deleteLater)
        return window

    def test_native_code_is_default_and_passes_without_sstoy_url_gate(self):
        window = self.window()
        self.assertEqual(window.input_kind.currentData(), "ingame")
        window.input_url.setText(CODE)
        window.input_name.setText("내 프리셋")
        with patch.object(build_maker, "ConverterWorker") as worker, patch.object(build_maker.QMessageBox, "warning") as warning:
            window.start_conversion()
            worker.assert_called_once_with(CODE, "ingame", "내 프리셋")
            worker.return_value.start.assert_called_once()
            warning.assert_not_called()
        self.assertFalse(window.input_kind.isEnabled())
        self.assertFalse(window.input_name.isEnabled())

    def test_sstoy_mode_keeps_url_validation(self):
        window = self.window()
        window.input_kind.setCurrentIndex(1)
        self.assertFalse(window.input_name.isEnabled())
        window.input_url.setText(CODE)
        with patch.object(build_maker, "ConverterWorker") as worker, patch.object(build_maker.QMessageBox, "warning") as warning:
            window.start_conversion()
            worker.assert_not_called()
            warning.assert_called_once()

    def test_converter_routes_native_import_to_exact_goal_adapter(self):
        worker = build_maker.ConverterWorker(CODE, "ingame", "목표")
        self.addCleanup(worker.deleteLater)
        with patch.object(build_maker.importlib, "reload"), patch.object(build_maker.formatter, "save_game_preset_to_json", return_value=(False, "checked")) as save, patch.object(build_maker.formatter, "save_build_to_json") as legacy:
            worker.run()
            save.assert_called_once_with(CODE, str(build_maker.PRESETS_DIR), "목표")
            legacy.assert_not_called()

    def test_real_converter_thread_saves_goals_and_reports_them_to_dialog(self):
        window = self.window()
        window.input_url.setText(CODE)
        window.input_name.setText("실제 코드")
        spy = QSignalSpy(window.conversion_finished)
        with tempfile.TemporaryDirectory() as folder, patch.object(build_maker, "PRESETS_DIR", Path(folder)), patch.object(build_maker.QMessageBox, "warning"), patch.object(build_maker.QMessageBox, "information"), patch.object(build_maker.QMessageBox, "critical") as critical:
            window.start_conversion()
            delivered = spy.wait(5000)
            window.worker.wait(5000)
            self.assertTrue(delivered, window.log_view.toPlainText())
            self.assertEqual(len(spy), 1)
            critical.assert_not_called()
            saved = Path(spy[0][0])
            self.assertEqual(saved.parent, Path(folder))
            loaded = BuildLoader(saved)
            self.assertEqual(loaded.get_target_level("donna", "513029.png"), 3)
            self.assertEqual(loaded.get_target_level("allie", "513912.png"), 2)
            self.assertIn("목표 Lv.3", window.log_view.toPlainText())
            self.assertIn("메인: 엘리", window.log_view.toPlainText())
            self.assertTrue(window.input_kind.isEnabled())
            self.assertTrue(window.input_name.isEnabled())


if __name__ == "__main__":
    unittest.main()
