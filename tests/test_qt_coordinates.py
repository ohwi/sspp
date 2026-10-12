"""Mock-mapping tests only; never import Qt or alter native/desktop DPI."""
import os
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from src.qt_coordinates import prepare_qt_pixel_coordinates, VALUES


class QtCoordinateTests(unittest.TestCase):
    def test_explicit_factors_are_normalized_and_unrelated_environment_is_preserved(self):
        environment={'QT_SCALE_FACTOR':'2','QT_SCREEN_SCALE_FACTORS':'1.5;1.5','QT_ENABLE_HIGHDPI_SCALING':'1','QT_AUTO_SCREEN_SCALE_FACTOR':'1','PATH':'unchanged'}
        receipt=prepare_qt_pixel_coordinates(environment)
        self.assertEqual({key:environment[key] for key in VALUES},VALUES)
        self.assertNotIn('QT_SCREEN_SCALE_FACTORS',environment)
        self.assertEqual(environment['PATH'],'unchanged')
        self.assertEqual(receipt['previous']['QT_SCALE_FACTOR'],'2')
        self.assertEqual(receipt['scope'],'supplied_mapping')

    def test_an_empty_mapping_does_not_fall_back_to_real_os_environ(self):
        actual={key:os.environ.get(key) for key in VALUES}
        environment={}
        prepare_qt_pixel_coordinates(environment)
        self.assertEqual(environment,VALUES)
        self.assertEqual({key:os.environ.get(key) for key in VALUES},actual)

    def test_default_scope_uses_an_injected_fake_environ(self):
        fake={'QT_SCALE_FACTOR':'2'}
        with patch('src.qt_coordinates.os.environ',fake):
            receipt=prepare_qt_pixel_coordinates()
        self.assertEqual(fake,VALUES)
        self.assertEqual(receipt['scope'],'current_process_environment')
        self.assertFalse(receipt['persistent_or_parent_settings_changed'])

    def test_repeated_call_is_idempotent(self):
        environment={}
        first=prepare_qt_pixel_coordinates(environment)
        second=prepare_qt_pixel_coordinates(environment)
        self.assertEqual(first['effective'],second['effective'])
        self.assertEqual(environment,VALUES)

    def test_immutable_or_invalid_input_is_not_modified(self):
        for value in ([],(),42,'bad'):
            with self.assertRaises(TypeError):prepare_qt_pixel_coordinates(value)


if __name__=='__main__':unittest.main()
