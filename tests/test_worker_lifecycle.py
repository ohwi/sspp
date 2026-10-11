"""Bounded worker lifecycle tests; no QThread.start or desktop capture."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from config import AppStatus
from src.worker import MatcherWorker


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sspp-worker-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.invalid = root / "invalid.json"
        self.valid = root / "valid.json"
        self.invalid.write_text(json.dumps({"characters": {"firenze": {"bad-id": 5}}}), encoding="utf-8")
        self.valid.write_text(json.dumps({"characters": {"firenze": {"513601": 5}}}), encoding="utf-8")

    def test_invalid_start_keeps_loop_available_for_valid_selection_and_play(self):
        worker = MatcherWorker(str(self.invalid))
        statuses = []
        loaded = []
        resets = []
        worker.status_signal.connect(lambda status, text: statuses.append((status, text)))
        worker.initial_load_finished.connect(lambda: loaded.append(True))
        worker.reset_signal.connect(lambda: resets.append(True))
        face = np.arange(100, dtype=np.uint8).reshape(10, 10)
        geo = {"x": 0, "y": 0, "w": 1280, "h": 720}
        pauses = []

        def simulated_sleep(seconds):
            pauses.append(seconds)
            if len(pauses) == 1:
                self.assertEqual(seconds, 0.5)
                self.assertTrue(worker.paused)
                self.assertIsNone(worker.build_loader)
                self.assertTrue(worker.running)
                self.assertFalse(loaded)
                self.assertEqual(statuses[-1][0], AppStatus.ERROR)
                self.assertTrue(resets)
                worker.update_build(str(self.valid))
                self.assertIsNotNone(worker.build_loader)
                self.assertTrue(worker.paused)
                self.assertTrue(worker.set_paused(False))

        def process_once(*args):
            self.assertFalse(worker.paused)
            self.assertEqual(worker.build_loader.get_priority("Firenze", "513601.png"), 5)
            worker.running = False

        with patch("src.worker.load_templates", return_value=({"firenze": (face, face)}, {})), \
                patch("src.worker.mss.mss") as capture_factory, \
                patch("src.worker.get_game_geometry", return_value=geo) as geometry, \
                patch("src.worker.time.sleep", side_effect=simulated_sleep), \
                patch.object(worker, "process_rois", side_effect=process_once) as process:
            worker.run()
        process.assert_called_once()
        geometry.assert_called_once()
        capture_factory.return_value.__enter__.return_value.grab.assert_not_called()
        self.assertEqual(statuses[-1][0], AppStatus.RUNNING)
        self.assertIsNone(worker.build_error)

    def test_invalid_selection_clears_previous_recommendations_and_refuses_play(self):
        worker = MatcherWorker(str(self.valid))
        worker.update_build(str(self.valid))
        self.assertTrue(worker.set_paused(False))
        self.assertEqual(worker.build_loader.get_priority("firenze", "513601.png"), 5)
        resets = []
        statuses = []
        worker.reset_signal.connect(lambda: resets.append(True))
        worker.status_signal.connect(lambda status, text: statuses.append((status, text)))
        worker.update_build(str(self.invalid))
        self.assertIsNone(worker.build_loader)
        self.assertTrue(worker.paused)
        self.assertTrue(resets)
        self.assertTrue(worker.set_paused(True))
        self.assertEqual(statuses[-1][0], AppStatus.ERROR)
        self.assertFalse(worker.set_paused(False))
        self.assertTrue(worker.paused)
        self.assertEqual(statuses[-1][0], AppStatus.ERROR)
        worker.update_build(str(self.valid))
        self.assertTrue(worker.paused)
        self.assertTrue(worker.set_paused(False))
        self.assertFalse(worker.paused)
        self.assertIsNone(worker.build_error)


if __name__ == "__main__":
    unittest.main()
