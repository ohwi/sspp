"""Confirmed native goal meanings and lossless readable descriptions."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.target_policy import target_description, target_label


class TargetPolicyTests(unittest.TestCase):
    def test_excluded_single_level_and_level_six_targets_have_distinct_meanings(self):
        for level, expected in ((0, "목표 제외"), (1, "Lv.1 확보"), (2, "Lv.1 확보"), (6, "목표 Lv.6")):
            with self.subTest(level=level):
                self.assertEqual(target_label(level), expected)
                description = "Lv.1 확보 (프리셋 Lv.2)" if level == 2 else expected
                self.assertEqual(target_description(level), description)

    def test_intermediate_levels_share_more_is_better_label_but_retain_original_in_description(self):
        descriptions = []
        for level in (3, 4, 5):
            self.assertEqual(target_label(level), "다다익선")
            description = target_description(level)
            self.assertEqual(description, f"다다익선 (프리셋 Lv.{level})")
            descriptions.append(description)
        self.assertEqual(len(set(descriptions)), 3)

    def test_invalid_levels_are_rejected_instead_of_coerced_to_a_goal(self):
        for value in (None, True, False, -1, 7, 2.0, "2"):
            for describe in (target_label, target_description):
                with self.subTest(value=value, function=describe.__name__):
                    with self.assertRaises(ValueError):
                        describe(value)


if __name__ == "__main__":
    unittest.main()
