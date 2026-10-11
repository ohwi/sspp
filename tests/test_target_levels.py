"""Exact native target contracts; fixture IDs are explicit, no game capture."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.character_catalog import load_catalog
from src.load_build import BuildLoader
from src.overlay import OverlayWindow
from src.worker import MatcherWorker


class TargetLevelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sspp-targets-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "preset.json"
        self.catalog = copy.deepcopy(load_catalog())
        for char_id in ("103", "111", "112"):
            # A small independent contract fixture supplies the documented five ordered arrays.
            base = 500000 + int(char_id) * 100
            self.catalog["characters"][char_id]["preset_schema"] = {
                "MasterSpecificPotentialIds": list(range(base + 1, base + 5)),
                "MasterNormalPotentialIds": list(range(base + 5, base + 14)),
                "AssistSpecificPotentialIds": list(range(base + 21, base + 25)),
                "AssistNormalPotentialIds": list(range(base + 25, base + 34)),
                "CommonPotentialIds": list(range(base + 41, base + 44)),
            }
        self.native = {"schema_version": 2, "source": {"type": "ingame_preset"},
                       "characters": {}, "target_levels": {}, "slots": []}
        for position, char_id in (("master", "103"), ("assist1", "111"), ("assist2", "112")):
            character = self.catalog["characters"][char_id]
            key = character["key"]
            schema = character["preset_schema"]
            role = "Master" if position == "master" else "Assist"
            specific = schema[f"{role}SpecificPotentialIds"]
            normal = schema[f"{role}NormalPotentialIds"] + schema["CommonPotentialIds"]
            levels = {str(pid): index % 2 for index, pid in enumerate(specific)}
            levels.update({str(pid): index % 7 for index, pid in enumerate(normal)})
            levels[str(normal[0])] = 2
            levels[str(normal[1])] = 3
            self.native["target_levels"][key] = levels
            self.native["characters"][key] = {pid: 2 if level > 0 else 0 for pid, level in levels.items()}
            self.native["slots"].append({"position": position, "character_id": int(char_id), "key": key})

    def load(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with patch("src.load_build.load_catalog", return_value=self.catalog):
            return BuildLoader(self.path)

    def test_native_levels_zero_through_six_survive_loading_without_priority_inference(self):
        loaded = self.load(self.native)
        self.assertTrue(loaded.is_ingame_preset)
        self.assertEqual(loaded.build_data, self.native)
        self.assertEqual(loaded.target_level_map, self.native["target_levels"])
        self.assertEqual(set(loaded.target_level_map["amber"].values()), set(range(7)))
        self.assertEqual(loaded.get_target_level("코하쿠", "510305.png"), 2)
        self.assertEqual(loaded.get_target_level("103", "510306.png"), 3)
        self.assertEqual(loaded.get_priority("amber", "510305.png"), 2)
        self.assertEqual(loaded.get_priority("amber", "510306.png"), 2)
        self.assertEqual(loaded.get_target_level("amber", "510301.png"), 0)
        self.assertIsNone(loaded.get_target_level("amber", "999999.png"))

    def test_legacy_sstoy_target_levels_do_not_change_overlay_semantics(self):
        legacy = {"characters": {"amber": {"510305": 5}},
                  "target_levels": {"amber": {"510305": 2}}, "source": {"type": "sstoy"}}
        loaded = self.load(legacy)
        self.assertEqual(loaded.get_priority("amber", "510305.png"), 5)
        self.assertIsNone(loaded.get_target_level("amber", "510305.png"))
        self.assertFalse(loaded.is_ingame_preset)
        self.assertEqual(OverlayWindow.match_style(5, loaded.get_target_level("amber", "510305.png")),
                         OverlayWindow.PRIORITY_STYLES[5])
        del legacy["source"]
        self.assertIsNone(self.load(legacy).get_target_level("amber", "510305.png"))

    def test_native_invalid_level_types_ranges_and_specific_level_are_rejected(self):
        for value in (True, -1, 7, "2", 2.0, None):
            with self.subTest(value=value):
                data = copy.deepcopy(self.native)
                data["target_levels"]["amber"]["510305"] = value
                with self.assertRaises(ValueError):
                    self.load(data)
        data = copy.deepcopy(self.native)
        data["target_levels"]["amber"]["510301"] = 2
        data["characters"]["amber"]["510301"] = 2
        with self.assertRaises(ValueError):
            self.load(data)

    def test_native_wrong_slot_role_missing_potential_and_priority_are_rejected(self):
        mutations = [
            lambda data: data["slots"].pop(),
            lambda data: data["slots"][0].update(character_id=0),
            lambda data: data["slots"][0].update(position="assist1"),
            lambda data: data["slots"][0].update(key="iris"),
            lambda data: data["target_levels"]["amber"].pop("510305"),
            lambda data: data["characters"]["amber"].update({"510305": 5}),
            lambda data: data.update(schema_version=1),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                data = copy.deepcopy(self.native)
                mutate(data)
                with self.assertRaises(ValueError):
                    self.load(data)

    def test_native_aliases_and_slot_list_order_preserve_exact_levels(self):
        data = copy.deepcopy(self.native)
        data["characters"]["코하쿠"] = data["characters"].pop("amber")
        data["target_levels"]["103"] = data["target_levels"].pop("amber")
        data["slots"].reverse()
        loaded = self.load(data)
        self.assertEqual(loaded.get_target_level("Amber", "510305.png"), 2)
        self.assertEqual(loaded.get_target_level("코하쿠", "510306.png"), 3)

    def test_native_overlay_labels_follow_the_confirmed_goal_policy(self):
        expected = {1: "Lv.1 확보", 2: "다다익선", 3: "다다익선", 4: "다다익선", 5: "다다익선", 6: "목표 Lv.6"}
        for level, label in expected.items():
            style = OverlayWindow.match_style(2, level)
            self.assertEqual(style[2], label)
            self.assertEqual(style[:2], OverlayWindow.match_style(5, level)[:2])
        self.assertEqual(OverlayWindow.match_style(0, 0)[2], "목표 제외")
        self.assertIsNone(OverlayWindow.match_style(0, None))
        self.assertIsNone(OverlayWindow.match_style(2, True))
        self.assertIsNone(OverlayWindow.match_style(2, 7))

    def test_worker_signal_passes_exact_level_two_three_and_zero(self):
        loader = self.load(self.native)
        worker = MatcherWorker(str(self.path))
        worker.build_loader = loader
        emitted = []
        worker.match_signal.connect(lambda *args: emitted.append(args))
        template = np.random.default_rng(18).integers(0, 255, size=(15, 15), dtype=np.uint8)
        geo = {"x": 0, "y": 0, "w": 1280, "h": 720}
        roi = {"x": 0.2, "y": 0.2, "w": 0.2, "h": 0.4}
        for index, filename in enumerate(("510305.png", "510306.png", "510301.png")):
            with patch("src.worker._capture_gray", return_value=template):
                worker.detect_skill(None, geo, roi, "amber", {"amber": {filename: template}}, 1.0, index)
        self.assertEqual([row[5] for row in emitted], [2, 3, 0])
        self.assertEqual([row[4] for row in emitted], [2, 2, 0])
        self.assertTrue(all(len(row) == 6 and row[3] for row in emitted))

    def test_failed_reload_clears_both_native_targets_and_legacy_priorities(self):
        loader = self.load(self.native)
        invalid = copy.deepcopy(self.native)
        invalid["target_levels"]["amber"]["510305"] = 7
        self.path.write_text(json.dumps(invalid), encoding="utf-8")
        with patch("src.load_build.load_catalog", return_value=self.catalog):
            self.assertFalse(loader.load_build(self.path))
        self.assertEqual(loader.target_map, {})
        self.assertEqual(loader.target_level_map, {})
        self.assertFalse(loader.is_ingame_preset)
        self.assertIsNone(loader.get_target_level("amber", "510305.png"))


if __name__ == "__main__":
    unittest.main()
