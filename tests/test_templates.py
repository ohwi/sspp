"""Offline resource-loading tests; never capture the desktop."""
import os
import pickle
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src import load_image


def write_png(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", image)
    assert success
    path.write_bytes(encoded.tobytes())


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sspp-한글-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "한글 리소스"
        self.face = np.arange(64, dtype=np.uint8).reshape(8, 8)
        self.skill = np.arange(81, dtype=np.uint8).reshape(9, 9)
        write_png(self.root / "icons" / "Firenze.png", self.face)
        write_png(self.root / "potentials" / "firenze" / "123.png", self.skill)

    def test_unicode_path_and_case_share_one_character_key(self):
        faces, skills = load_image.load_templates(self.root)
        self.assertEqual(set(faces), {"firenze"})
        self.assertEqual(set(skills), {"firenze"})
        np.testing.assert_array_equal(faces["firenze"][0], self.face)
        np.testing.assert_array_equal(skills["firenze"]["123.png"], self.skill)
        self.assertEqual(faces["firenze"][0].shape, faces["firenze"][1].shape)

    def test_valid_cache_avoids_decoding_again(self):
        load_image.load_templates(self.root)
        with patch.object(load_image, "_read_gray_image", side_effect=AssertionError("cache miss")):
            faces, skills = load_image.load_templates(self.root)
        self.assertIn("firenze", faces)
        self.assertIn("123.png", skills["firenze"])

    def test_deleted_skill_is_removed_from_cache(self):
        load_image.load_templates(self.root)
        (self.root / "potentials" / "firenze" / "123.png").unlink()
        faces, skills = load_image.load_templates(self.root)
        self.assertIn("firenze", faces)
        self.assertNotIn("firenze", skills)

    def test_replaced_image_with_preserved_mtime_is_reloaded(self):
        load_image.load_templates(self.root)
        path = self.root / "icons" / "Firenze.png"
        previous_stat = path.stat()
        replacement = np.flipud(self.face).copy()
        write_png(path, replacement)
        os.utime(path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
        faces, _ = load_image.load_templates(self.root)
        np.testing.assert_array_equal(faces["firenze"][0], replacement)

    def test_old_schema_cannot_restore_wrong_character_case(self):
        cache = {"face": {"Firenze": (self.face, self.face)}, "skill": {}}
        with (self.root / "templates.cache").open("wb") as stream:
            pickle.dump(cache, stream)
        faces, skills = load_image.load_templates(self.root)
        self.assertEqual(set(faces), {"firenze"})
        self.assertIn("123.png", skills["firenze"])

    def test_corrupt_cache_and_image_do_not_prevent_other_assets_loading(self):
        (self.root / "templates.cache").write_bytes(b"not a pickle")
        (self.root / "icons" / "corrupt.png").write_bytes(b"not an image")
        faces, skills = load_image.load_templates(self.root)
        self.assertEqual(set(faces), {"firenze"})
        self.assertIn("123.png", skills["firenze"])


if __name__ == "__main__":
    unittest.main()
