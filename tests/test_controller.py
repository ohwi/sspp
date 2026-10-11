import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PyQt5.QtWidgets import QApplication
from src import main
from src.sstoy_loader import build_maker


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, directory):
        patches = [patch.object(main, "PRESETS_DIR", directory),
                   patch.object(main, "MatcherWorker", return_value=MagicMock()),
                   patch.object(main, "OverlayWindow", return_value=MagicMock())]
        for current in patches:
            current.start()
            self.addCleanup(current.stop)
        panel = main.ControlPanel()
        self.addCleanup(panel.close)
        return panel

    def preset(self, directory, name, key="suntide_willow"):
        path = directory / name
        path.write_text(json.dumps({"characters": {key: {"516021": 2}}}), encoding="utf-8")
        return path

    def test_created_build_is_selected_and_worker_receives_its_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.preset(directory, "example_build.json")
            panel = self.make_panel(directory)
            created = self.preset(directory, "새 빌드.json")
            panel.on_build_created(str(created))
            self.assertEqual(panel.build_combo.currentText(), created.name)
            panel.worker.update_build.assert_called_with(str(created))
            self.assertIn("준비", panel.coverage_label.text())

    def test_refresh_reloads_modified_current_preset(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = self.preset(directory, "example_build.json")
            panel = self.make_panel(directory)
            panel.worker.update_build.reset_mock()
            path.write_text(json.dumps({"characters":{"chaton":{"511401":2}}}))
            panel.refresh_build_list()
            panel.worker.update_build.assert_called_once_with(str(path))
            self.assertIn("얼굴 1명", panel.coverage_label.text())

    def test_invalid_selection_updates_the_play_button_to_paused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.preset(directory, "example_build.json")
            panel = self.make_panel(directory)
            panel.is_monitoring = True
            panel.worker.paused = True
            panel.on_build_changed("example_build.json")
            self.assertFalse(panel.is_monitoring)
            self.assertFalse(panel.btn_toggle.isChecked())
            self.assertEqual(panel.btn_toggle.text(), "▶")

    def test_refresh_clears_removed_last_preset(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = self.preset(directory, "example_build.json")
            panel = self.make_panel(directory)
            path.unlink()
            panel.refresh_build_list()
            panel.worker.update_build.assert_called_with("")
            panel.worker.set_paused.assert_called_with(True)
            self.assertIn("없습니다", panel.coverage_label.text())

    def test_import_dialog_handles_an_unreadable_saved_preset(self):
        widget = build_maker.BuildMaker()
        self.addCleanup(widget.close)
        received = []
        widget.conversion_finished.connect(received.append)
        with patch.object(build_maker.QMessageBox, "critical") as error:
            widget.on_finished(True, "missing-after-import.json")
        error.assert_called_once()
        self.assertEqual(received, [])

    def test_import_dialog_warns_for_missing_images_and_emits_saved_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            saved = Path(temporary) / "빌드.json"
            saved.write_text(json.dumps({"warnings":["샤통: 얼굴 이미지 없음"]}),encoding="utf-8")
            widget = build_maker.BuildMaker()
            self.addCleanup(widget.close)
            received = []
            widget.conversion_finished.connect(received.append)
            with patch.object(build_maker.QMessageBox,"warning") as warn, patch.object(build_maker.QMessageBox,"information") as info:
                widget.on_finished(True,str(saved))
            warn.assert_called_once()
            info.assert_not_called()
            self.assertEqual(received,[str(saved)])


if __name__ == "__main__":
    unittest.main()
