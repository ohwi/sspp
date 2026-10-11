"""Protocol interoperability tests; fixtures come from pinned upstream encoders."""
import hashlib
import importlib.util
import json
import logging
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sstoy_decoder_under_test", ROOT / "app/src/sstoy_loader/decoder.py"
)
decoder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decoder)
FIXTURE_DIR = ROOT / "tests/fixtures"
FIXTURE = json.loads((FIXTURE_DIR / "sstoy_urls.json").read_text(encoding="utf-8"))


def json_keys(value):
    """JSON object keys are strings; normalize the decoder's integer keys."""
    return json.loads(json.dumps(value, ensure_ascii=False))


class SSToyDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("SSToyDecoder").disabled = True

    def assert_bad_url(self, url):
        result = decoder.decode_url_raw(url)
        self.assertIn("error", result, result)
        self.assertNotIn("raw_characters", result)
        return result["error"]

    def test_upstream_v2_and_v3_interoperability(self):
        for fixture in FIXTURE["cases"]:
            with self.subTest(name=fixture["name"]):
                self.assertEqual(json_keys(decoder.decode_url_raw(fixture["token"])), fixture["expected"])
                url = fixture.get("url") or (
                    "https://jforplay.github.io/sstoy/app.html#build=" +
                    quote(fixture["token"], safe="")
                )
                self.assertEqual(json_keys(decoder.decode_url_raw(url)), fixture["expected"])

    def test_base91_14_bit_interoperability_vector(self):
        vector = FIXTURE["base91_vector"]
        self.assertEqual(decoder.base91_decode(vector["encoded"]), bytes.fromhex(vector["hex"]))

    def test_base32768_uses_upstream_unicode_alphabet(self):
        vector = FIXTURE["raw_vector"]
        self.assertEqual(decoder.base32768_decode(vector["encoded"]), bytes.fromhex(vector["hex"]))

    def test_upstream_raw_data_loss_is_reported(self):
        for fixture in FIXTURE["raw_regression"]:
            with self.subTest(version=fixture["version"]):
                self.assertEqual(fixture["alphabet_entries"], 511)
                self.assertIn("undefined", fixture["token"])
                self.assertIn("유실", self.assert_bad_url(fixture["token"]))

    def test_v2_uses_exact_frozen_maps(self):
        provenance = FIXTURE["provenance"]
        self.assertEqual(list(decoder.LEGACY_V2_CHAR_IDS), provenance["frozen_v2_character_ids"])
        self.assertEqual(list(decoder.LEGACY_V2_POT_IDS), provenance["frozen_v2_potential_ids"])
        # End-of-map IDs are also exercised in the v2 assist slot fixture.
        self.assertEqual(decoder.LEGACY_V2_CHAR_IDS[-1], 159)
        self.assertEqual(decoder.LEGACY_V2_POT_IDS[-1], 515943)

    def test_upstream_mark_semantics(self):
        fixture = FIXTURE["cases"][0]
        result = decoder.decode_url_raw(fixture["token"])
        marks = result["raw_characters"]["master"]["marks"]
        self.assertEqual(marks, {510301: 5, 510302: 1, 510303: 4, 510304: 2})
        self.assertNotIn(510305, marks)  # Unmarked selections use formatter's default.

    def test_query_and_fragment_parameters(self):
        fixture = FIXTURE["cases"][0]
        token = quote(fixture["token"], safe="")
        for url in (
            "https://example.com/app.html?lang=KR&build=" + token,
            "https://example.com/app.html?lang=KR#build=" + token + "&view=characters",
            "#build=" + token,
            "build=" + token,
        ):
            with self.subTest(url=url[:60]):
                self.assertEqual(json_keys(decoder.decode_url_raw(url)), fixture["expected"])

    def test_invalid_and_ambiguous_urls_fail_without_partial_result(self):
        fixture = FIXTURE["cases"][0]
        token = quote(fixture["token"], safe="")
        for url in (
            None, 3, "", "https://example.com/app.html",
            "https://example.com/?build=" + token + "#build=" + token,
            "https://example.com/#build=v3d-%ZZ",
            "https://example.com/#build=v3d-%FF",
            "v3d-", "v3d-INVALID SPACE", "v3r-😀", "N4IglegacyLZString",
            "v3d-" + "A" * decoder.MAX_URL_CHARS,
        ):
            with self.subTest(url=str(url)[:60]):
                self.assert_bad_url(url)

    def test_malformed_protocol_and_compression_fixtures(self):
        for fixture in FIXTURE["malformed"]:
            with self.subTest(name=fixture["name"]):
                self.assert_bad_url(fixture["token"])

    def test_discs_flag_is_required_after_character_payloads(self):
        fixtures = {fixture["name"]: fixture for fixture in FIXTURE["malformed"]}
        for version in (2, 3):
            with self.subTest(version=version):
                fixture = fixtures[f"v{version}_missing_discs_flag"]
                self.assertIn("잘렸습니다", self.assert_bad_url(fixture["token"]))

    def test_varints_reject_noncanonical_truncated_or_overflowing_values(self):
        self.assertEqual(decoder.read_varint(b"\xff\xff\xff\xff\x0f", 0), (4294967295, 5))
        self.assertEqual(decoder.read_varint(b"\xac\x02", 0), (300, 2))
        for value in (b"", b"\x80", b"\x80\x00", b"\xff\xff\xff\xff\x10"):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, EOFError)):
                    decoder.read_varint(value, 0)

    def test_pinned_encoder_excerpt_receipts(self):
        provenance = FIXTURE["provenance"]
        for version in ("v2", "v3"):
            digest = hashlib.sha256((FIXTURE_DIR / ("sstoy_" + version + "_reference.ts")).read_bytes()).hexdigest()
            self.assertEqual(digest, provenance[version + "_excerpt_sha256"])


if __name__ == "__main__":
    unittest.main()
