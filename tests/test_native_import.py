"""Real game code -> saved preset -> loader, including exact level 2/3 goals."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.load_build import BuildLoader
from src.sstoy_loader import formatter

USER_CODE = "AAAAiwAAAKAAAACCMAbDZDDNlgAABs2AQDwA"


class NativeImportTests(unittest.TestCase):
    def test_users_code_preserves_roles_and_all_exact_goals_after_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, saved = formatter.save_game_preset_to_json(USER_CODE, folder)
            self.assertTrue(ok, saved)
            data = json.loads(Path(saved).read_text(encoding="utf-8"))
            self.assertEqual([(s["position"], s["character_id"]) for s in data["slots"]],
                             [("master", 139), ("assist1", 160), ("assist2", 130)])
            self.assertEqual(data["source"]["type"], "ingame_preset")
            self.assertEqual(data["source"]["code"], USER_CODE)
            self.assertEqual(sum(len(p) for p in data["target_levels"].values()), 48)
            self.assertEqual(sum(v > 0 for p in data["target_levels"].values() for v in p.values()), 22)
            loaded = BuildLoader(saved)
            for character, expected in {
                "allie": {"513903":1, "513904":1, "513908":6, "513909":6,
                          "513910":6, "513912":2, "513913":6, "513942":6},
                "suntide_willow": {"516021":1, "516022":1, "516025":6, "516026":6,
                                   "516027":2, "516028":6, "516043":6},
                "donna": {"513021":1, "513022":1, "513025":6, "513026":6,
                          "513029":3, "513030":2, "513033":6},
            }.items():
                actual = {pid: level for pid, level in data["target_levels"][character].items() if level > 0}
                self.assertEqual(actual, expected)
                for pid, level in expected.items():
                    self.assertEqual(loaded.get_target_level(character, pid + ".png"), level)
            self.assertEqual(loaded.get_target_level("윌로(여름)", "516023.png"), 0)
            self.assertEqual(loaded.get_target_level("160", "516027.png"), 2)
            self.assertEqual(sum(len(c["missing_potential_ids"]) for c in data["template_coverage"].values()), 20)
            self.assertEqual(sum(c["selected_count"] for c in data["template_coverage"].values()), 22)
            self.assertEqual(sum(not c["face_available"] for c in data["template_coverage"].values()), 2)
            self.assertTrue(data["warnings"])

    def test_readable_summary_preserves_intermediate_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, saved = formatter.save_game_preset_to_json(USER_CODE, folder)
            self.assertTrue(ok, saved)
            summary = formatter.describe_game_preset(json.loads(Path(saved).read_text("utf-8")))
            for text in ("메인: 엘리", "서포트 1: 윌로(여름)", "서포트 2: 토나", "Lv.1 확보", "다다익선 (프리셋 Lv.2)", "다다익선 (프리셋 Lv.3)", "목표 Lv.6", "먼지 제거 소용돌이"):
                self.assertIn(text, summary)

    def test_named_repeat_import_keeps_original_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, saved = formatter.save_game_preset_to_json(USER_CODE, folder, "내 프리셋")
            self.assertTrue(ok, saved)
            original = Path(saved).read_bytes()
            again, second = formatter.save_game_preset_to_json(USER_CODE, folder, "내 프리셋")
            self.assertTrue(again, second)
            self.assertEqual(Path(second).name, "내 프리셋 (2).json")
            self.assertEqual(Path(saved).read_bytes(), original)

    def test_invalid_game_code_cannot_leave_a_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = formatter.save_game_preset_to_json(USER_CODE[:-4], folder)
            self.assertFalse(ok)
            self.assertIn("게임 프리셋", message)
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
