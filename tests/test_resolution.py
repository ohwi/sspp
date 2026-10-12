"""Pure geometry/observed-pixel tests; never capture or query a live window."""
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.load_resolution import get_capture_area, get_reference_scale, resolve_content_geometry
from config import ROIS, FACE_OFFSET


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(37)

    def content(self, height, width):
        return self.rng.integers(20, 240, (height, width, 3), dtype=np.uint8)

    def test_without_observation_an_arbitrary_aspect_is_never_assumed_letterboxed(self):
        for width, height in [(1280,1024),(1920,810),(2560,1080)]:
            geo = {"x":-1800,"y":-250,"w":width,"h":height}
            result, diagnostic = resolve_content_geometry(geo)
            self.assertEqual(result, geo)
            self.assertFalse(diagnostic["applied"])

    def test_top_bottom_bars_preserve_absolute_origin_and_content_scale(self):
        image = np.zeros((1024,1280,3), np.uint8)
        image[152:872] = self.content(720,1280)
        geo = {"x":-1800,"y":100,"w":1280,"h":1024}
        content, diagnostic = resolve_content_geometry(geo,image)
        self.assertEqual(content,{"x":-1800,"y":252,"w":1280,"h":720})
        self.assertTrue(diagnostic["applied"])
        self.assertEqual(get_reference_scale(content),1)
        self.assertEqual(get_capture_area(content,ROIS[0],FACE_OFFSET)["top"],383)

    def test_side_bars_use_content_width_and_ignore_alpha(self):
        image = np.zeros((810,1920,4), np.uint8)
        image[:,:,3] = 255
        image[:,240:1680,:3] = self.content(810,1440)
        content, diagnostic = resolve_content_geometry({"x":20,"y":30,"w":1920,"h":810},image)
        self.assertEqual(content,{"x":260,"y":30,"w":1440,"h":810})
        self.assertTrue(diagnostic["applied"])
        self.assertAlmostEqual(get_reference_scale(content),1280/1440)

    def test_reference_aspect_is_unchanged(self):
        geo={"x":0,"y":0,"w":1280,"h":720}
        self.assertEqual(resolve_content_geometry(geo,self.content(720,1280))[0],geo)

    def test_stretched_nonblack_content_is_not_cropped(self):
        geo={"x":0,"y":0,"w":1280,"h":1024}
        content, diagnostic=resolve_content_geometry(geo,self.content(1024,1280))
        self.assertEqual(content,geo)
        self.assertFalse(diagnostic["applied"])

    def test_uniform_black_or_gray_cannot_supply_content_evidence(self):
        geo={"x":0,"y":0,"w":1920,"h":810}
        for value in (0,3,128):
            content, diagnostic=resolve_content_geometry(geo,np.full((810,1920,3),value,np.uint8))
            self.assertEqual(content,geo)
            self.assertFalse(diagnostic["applied"])

    def test_asymmetric_or_wrong_aspect_bars_are_not_used(self):
        geo={"x":0,"y":0,"w":1920,"h":810}
        for left,right in [(200,280),(100,100)]:
            image=np.zeros((810,1920,3),np.uint8)
            image[:,left:1920-right]=self.content(810,1920-left-right)
            content,diagnostic=resolve_content_geometry(geo,image)
            self.assertEqual(content,geo)
            self.assertFalse(diagnostic["applied"])

    def test_nonblack_borders_do_not_prove_letterbox(self):
        geo={"x":0,"y":0,"w":1920,"h":810}
        image=np.full((810,1920,3),128,np.uint8)
        image[:,240:1680]=self.content(810,1440)
        self.assertEqual(resolve_content_geometry(geo,image)[0],geo)

    def test_mismatched_pixel_size_and_invalid_arrays_are_ignored(self):
        geo={"x":0,"y":0,"w":1920,"h":810}
        for image in (self.content(720,1280),np.zeros((810,1920,2),np.uint8),np.zeros((810,1920,3),np.float32)):
            self.assertEqual(resolve_content_geometry(geo,image)[0],geo)

    def test_scale_rejects_invalid_dimensions_and_uses_pixels_not_dpi(self):
        for width in (960,1024,1280,1366,1600,1920,2560):
            self.assertAlmostEqual(get_reference_scale({"w":width,"h":round(width*9/16)}),1280/width)
        for width,height in [(0,720),(1280,0),(float("nan"),720),(1280,float("inf"))]:
            with self.assertRaises(ValueError):
                get_reference_scale({"w":width,"h":height})


if __name__ == "__main__":
    unittest.main()
