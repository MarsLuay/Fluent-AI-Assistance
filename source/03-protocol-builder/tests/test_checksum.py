"""Tests for the vendored pure-Python FluentControl checksum implementation."""
from __future__ import annotations

import hashlib
import unittest

from fluent_pipeline import checksum
from fluent_pipeline._checksum_fixtures import KNOWN_GOOD_ENTRIES


def _stored_checksum(entry: bytes) -> str:
    match = checksum._CHECKSUM_BYTES_RE.search(entry)
    assert match is not None
    return match.group(2).strip().decode("ascii")


class VendoredChecksumTests(unittest.TestCase):
    def test_fixtures_exist_and_cover_both_branches(self):
        self.assertGreaterEqual(len(KNOWN_GOOD_ENTRIES), 3)
        roots = {checksum.xml_root_name(e) for e in KNOWN_GOOD_ENTRIES}
        # at least one MD5 (VxData) and one SHA-256 metadata root present
        self.assertIn("VxData", roots)
        self.assertTrue(roots & set(checksum.SHA256_PAYLOAD_ROOTS))

    def test_compute_reproduces_every_fixture_checksum(self):
        for entry in KNOWN_GOOD_ENTRIES:
            with self.subTest(root=checksum.xml_root_name(entry)):
                self.assertEqual(checksum.compute_checksum(entry), _stored_checksum(entry))

    def test_verify_self_passes(self):
        self.assertTrue(checksum.verify_self())

    def test_md5_branch_is_uppercase_hex_32(self):
        vx = next(e for e in KNOWN_GOOD_ENTRIES if checksum.xml_root_name(e) == "VxData")
        value = checksum.compute_checksum(vx)
        assert value is not None
        self.assertEqual(len(value), 32)
        self.assertEqual(value, value.upper())

    def test_sha256_branch_is_uppercase_hex_64(self):
        meta = next(
            e for e in KNOWN_GOOD_ENTRIES if checksum.xml_root_name(e) in checksum.SHA256_PAYLOAD_ROOTS
        )
        value = checksum.compute_checksum(meta)
        assert value is not None
        self.assertEqual(len(value), 64)
        self.assertEqual(value, value.upper())

    def test_stamp_round_trips_blanked_entry(self):
        for entry in KNOWN_GOOD_ENTRIES:
            with self.subTest(root=checksum.xml_root_name(entry)):
                blanked = checksum._blank_checksum(entry)
                self.assertNotEqual(blanked, entry)
                self.assertEqual(checksum.stamp_checksum(blanked), entry)

    def test_stamp_handles_self_closing_checksum(self):
        entry = b"<VxData><Payload><A>1</A></Payload><Checksum /></VxData>"
        stamped = checksum.stamp_checksum(entry)
        self.assertIsNotNone(stamped)
        assert stamped is not None
        self.assertIn(b"<Checksum>", stamped)
        self.assertNotIn(b"<Checksum />", stamped)
        self.assertEqual(checksum.compute_checksum(stamped), checksum.compute_checksum(entry))

    def test_compute_returns_none_without_payload(self):
        self.assertIsNone(checksum.compute_checksum(b"<Root><Checksum></Checksum></Root>"))

    def test_stamp_returns_none_without_checksum_element(self):
        self.assertIsNone(checksum.stamp_checksum(b"<Root><Payload>x</Payload></Root>"))

    def test_inter_tag_whitespace_is_collapsed_but_text_preserved(self):
        # Two entries differing only by inter-tag whitespace hash identically;
        # text-node whitespace differences must change the digest.
        a = b"<VxData><Payload>\n  <A>x</A>\n  <B>y z</B>\n</Payload><Checksum></Checksum></VxData>"
        b = b"<VxData><Payload><A>x</A><B>y z</B></Payload><Checksum></Checksum></VxData>"
        c = b"<VxData><Payload><A>x</A><B>yz</B></Payload><Checksum></Checksum></VxData>"
        self.assertEqual(checksum.compute_checksum(a), checksum.compute_checksum(b))
        self.assertNotEqual(checksum.compute_checksum(b), checksum.compute_checksum(c))

    def test_known_algorithm_is_md5_over_collapsed_inner_payload(self):
        # Lock in the exact algorithm against a hand-computed expectation.
        entry = b"<VxData><Payload> <A>1</A> </Payload><Checksum></Checksum></VxData>"
        expected = hashlib.md5(b"<A>1</A>").hexdigest().upper()
        self.assertEqual(checksum.compute_checksum(entry), expected)


if __name__ == "__main__":
    unittest.main()
