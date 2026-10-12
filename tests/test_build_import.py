import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.character_catalog import load_catalog, template_coverage
from src.load_build import BuildLoader
from src.sstoy_loader import formatter


def decoded(char_id, potentials, marks=None, name="테스트"):
    return {"build_name": name, "version": 3, "raw_characters": {"master": {
        "mapped_char_idx": char_id, "mapped_potentials": potentials,
        "marks": marks or {}, "potential_levels": {p: 1 for p in potentials}}}}


class BuildImportTests(unittest.TestCase):
    def import_build(self, build, folder):
        with patch.object(formatter.decoder, "decode_url_raw", return_value=build):
            return formatter.save_build_to_json("fixture", folder)

    def test_nazuka_name_and_id_use_existing_natsuka_templates(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, saved = self.import_build(decoded(133, [513306], {513306: 5}), folder)
            self.assertTrue(ok, saved)
            result = json.loads(Path(saved).read_text("utf-8"))
            self.assertIn("natsuka", result["characters"])
            loaded = BuildLoader(saved)
            self.assertEqual(loaded.get_priority("Nazuka", "513306.png"), 5)
            self.assertEqual(loaded.get_priority("133", "513306.png"), 5)
            self.assertEqual(result["warnings"], [])

    def test_single_level_essential_gets_level_one_label(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, saved = self.import_build(decoded(103, [510301], {510301: 5}), folder)
            self.assertTrue(ok, saved)
            self.assertEqual(BuildLoader(saved).get_priority("amber", "510301.png"), 3)

    def test_new_character_metadata_has_registered_potential_and_explicit_missing_face(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, saved = self.import_build(decoded(114, [511401]), folder)
            self.assertTrue(ok, saved)
            result = json.loads(Path(saved).read_text("utf-8"))
            self.assertIn("chaton", result["characters"])
            self.assertEqual(result["template_coverage"]["chaton"]["missing_potential_ids"], [])
            self.assertFalse(result["template_coverage"]["chaton"]["face_available"])
            self.assertTrue(result["warnings"])

    def test_foreign_potential_does_not_create_a_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = self.import_build(decoded(160, [510301]), folder)
            self.assertFalse(ok)
            self.assertIn("510301", message)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_unknown_character_does_not_create_empty_success(self):
        with tempfile.TemporaryDirectory() as folder:
            ok, message = self.import_build(decoded(99999, [510301]), folder)
            self.assertFalse(ok)
            self.assertIn("99999", message)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_import_keeps_existing_preset_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = Path(folder) / "테스트.json"
            previous.write_bytes(b"retain-this-preset")
            ok, saved = self.import_build(decoded(160, [516021]), folder)
            self.assertTrue(ok, saved)
            self.assertEqual(previous.read_bytes(), b"retain-this-preset")
            self.assertEqual(Path(saved).name, "테스트 (2).json")

    def test_bad_priority_cannot_survive_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "bad.json"
            target.write_text(json.dumps({"characters":{"amber":{"510301": True}}}))
            with self.assertRaises(ValueError):
                BuildLoader(target)

    def test_fixed_legacy_filenames_and_capture_images_are_covered(self):
        catalog = load_catalog()
        self.assertEqual(len(catalog["characters"]), 40)
        self.assertEqual(sum(len(character["potentials"]) for character in catalog["characters"].values()), 1160)
        coverage = template_coverage({"freesia":{"512513":2}, "natsuka":{"513306":2},
                                     "mistique":{"513508":2},
                                     "suntide_willow":{"516021":2,"516022":2,"516023":2}})
        self.assertTrue(all(row["face_available"] and not row["missing_potential_ids"] for row in coverage.values()))

    def test_only_name_rows_are_selected_from_remote_text_table(self):
        self.assertEqual(formatter.build_id_mapping({"Character.133.1":"Nazuka",
                         "Character.133.2":"Different biography", "Character.133.10":"Different title"}), {133:"Nazuka"})

    def test_windows_reserved_and_path_names_are_sanitized(self):
        self.assertEqual(formatter.sanitize_filename("CON"), "_CON")
        self.assertEqual(formatter.sanitize_filename("../x\\y"), "..xy")


if __name__ == "__main__":
    unittest.main()
