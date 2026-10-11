"""Native game export and shipped preset interoperability, without game input."""
import base64
import copy
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from src.ingame_preset import decode_preset

FIXTURE = json.loads(
    (ROOT / "tests/fixtures/ingame_presets/cases.json").read_text(encoding="utf-8")
)
CATALOG = FIXTURE["catalog"]
USER = FIXTURE["cases"][0]


def encoded(payload):
    return base64.b64encode(payload).decode("ascii")


def json_keys(value):
    return json.loads(json.dumps(value))


class IngamePresetTests(unittest.TestCase):
    def test_user_and_shipped_codes_match_independent_upstream_oracle(self):
        for fixture in FIXTURE["cases"]:
            with self.subTest(name=fixture["name"]):
                result = decode_preset(fixture["code"], CATALOG)
                self.assertEqual(json_keys(result["slots"]), fixture["expected_slots"])
                self.assertEqual(result["source"]["payload_hex"], fixture["payload_hex"])
                self.assertEqual(result["source"]["format"], "stella_sora_ingame_preset")
                self.assertEqual(result["source"]["code"], fixture["code"])

    def test_user_targets_preserve_unsorted_id_order_and_exact_levels(self):
        slots = decode_preset(USER["code"], CATALOG)["slots"]
        self.assertEqual([slot["char_id"] for slot in slots], [139, 160, 130])
        self.assertEqual([slot["position"] for slot in slots], ["master", "assist1", "assist2"])
        self.assertEqual(
            {pid: level for pid, level in slots[0]["target_levels"].items() if level},
            {513903: 1, 513904: 1, 513908: 6, 513909: 6, 513910: 6,
             513912: 2, 513913: 6, 513942: 6},
        )
        self.assertEqual(slots[2]["target_levels"][513029], 3)
        self.assertEqual(slots[2]["target_levels"][513030], 2)
        self.assertEqual([len(slot["target_levels"]) for slot in slots], [16, 16, 16])
        self.assertEqual(slots[0]["target_levels"][513901], 0)
        # Catalog's individual potential maxima do not cap native preset targets.
        self.assertEqual(CATALOG["characters"]["139"]["potentials"]["513908"], 3)
        self.assertEqual(slots[0]["target_levels"][513908], 6)

    def test_default_catalog_loader_contract(self):
        with patch("src.character_catalog.load_catalog", return_value=CATALOG):
            self.assertEqual(
                decode_preset(USER["code"])["slots"],
                decode_preset(USER["code"], CATALOG)["slots"],
            )

    def test_whitespace_and_missing_base64_padding_preserve_original_source(self):
        code = " \n" + USER["code"][:18] + "\t" + USER["code"][18:] + "\r\n"
        result = decode_preset(code, CATALOG)
        self.assertEqual(result["source"]["code"], code)
        self.assertEqual(json_keys(result["slots"]), USER["expected_slots"])
        # A schema with one additional flag needs one extra byte and '==' padding.
        catalog = copy.deepcopy(CATALOG)
        character = catalog["characters"]["139"]
        character["preset_schema"]["MasterSpecificPotentialIds"].append(999999)
        character["potentials"]["999999"] = 1
        payload = struct.pack(">III", 139, 160, 130) + bytes(16)
        code = encoded(payload)
        self.assertTrue(code.endswith("=="))
        self.assertEqual(
            decode_preset(code.rstrip("="), catalog)["slots"],
            decode_preset(code, catalog)["slots"],
        )

    def test_malformed_base64_is_rejected(self):
        for code in (None, 1, "", " ", "A", "AA===", "AA=A", "AA-_",
                     USER["code"] + "!", "Ab==", "A" * 4097):
            with self.subTest(code=str(code)[:50]):
                with self.assertRaises(ValueError):
                    decode_preset(code, CATALOG)

    def test_truncated_and_extra_payload_are_rejected(self):
        payload = bytes.fromhex(USER["payload_hex"])
        for bad in (payload[:11], payload[:-1], payload + b"\0", payload + b"\x01"):
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError):
                    decode_preset(encoded(bad), CATALOG)

    def test_unknown_zero_and_duplicate_characters_are_rejected(self):
        original = bytes.fromhex(USER["payload_hex"])
        for ids in ((999999, 160, 130), (0, 160, 130), (139, 139, 130)):
            with self.subTest(ids=ids):
                bad = struct.pack(">III", *ids) + original[12:]
                with self.assertRaises(ValueError):
                    decode_preset(encoded(bad), CATALOG)

    def test_three_bit_level_seven_is_rejected(self):
        payload = bytearray.fromhex(USER["payload_hex"])
        # Master special flags consume four bits; first normal level is bits 4..6.
        payload[12] |= 0x0e
        with self.assertRaisesRegex(ValueError, "범위"):
            decode_preset(encoded(payload), CATALOG)

    def test_nonzero_tail_padding_is_rejected(self):
        catalog = copy.deepcopy(CATALOG)
        catalog["characters"]["139"]["preset_schema"]["MasterSpecificPotentialIds"].pop()
        # 39 + 40 + 40 body bits; one final pad bit remains in these 15 bytes.
        payload = struct.pack(">III", 139, 160, 130) + bytes(15)
        valid = decode_preset(encoded(payload), catalog)
        self.assertTrue(all(level == 0 for slot in valid["slots"]
                            for level in slot["target_levels"].values()))
        with self.assertRaisesRegex(ValueError, "패딩"):
            decode_preset(encoded(payload[:-1] + b"\x01"), catalog)

    def test_incomplete_or_ambiguous_catalog_is_rejected(self):
        catalogs = []
        missing_max = copy.deepcopy(CATALOG)
        del missing_max["ingame_preset_max_level"]
        catalogs.append(missing_max)
        bad_max = copy.deepcopy(CATALOG)
        bad_max["ingame_preset_max_level"] = True
        catalogs.append(bad_max)
        missing_schema = copy.deepcopy(CATALOG)
        del missing_schema["characters"]["139"]["preset_schema"]
        catalogs.append(missing_schema)
        duplicate_pid = copy.deepcopy(CATALOG)
        duplicate_pid["characters"]["139"]["preset_schema"]["CommonPotentialIds"][0] = 513901
        catalogs.append(duplicate_pid)
        unknown_pid = copy.deepcopy(CATALOG)
        unknown_pid["characters"]["139"]["preset_schema"]["CommonPotentialIds"][0] = 999999
        catalogs.append(unknown_pid)
        bad_array = copy.deepcopy(CATALOG)
        bad_array["characters"]["139"]["preset_schema"]["CommonPotentialIds"] = [True]
        catalogs.append(bad_array)
        for catalog in catalogs:
            with self.subTest(index=catalogs.index(catalog)):
                with self.assertRaises(ValueError):
                    decode_preset(USER["code"], catalog)


if __name__ == "__main__":
    unittest.main()
