from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from fluent_pipeline import checksums
from fluent_pipeline._checksum_fixtures import KNOWN_GOOD_ENTRIES


def _reset_checksum_backend() -> None:
    checksums._BRIDGE_CACHE = checksums._UNRESOLVED


class ChecksumBackendDiscoveryTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("TECAN_CHECKSUM_BACKEND", None)
        _reset_checksum_backend()

    def test_no_backend_fails_closed(self):
        os.environ["TECAN_CHECKSUM_BACKEND"] = "none"
        _reset_checksum_backend()

        data = b"<Root><Payload></Payload><Checksum></Checksum></Root>"

        self.assertFalse(checksums.checksum_bridge_available())
        self.assertIsNone(checksums.recompute_checksum_bytes(data))

    def test_python_file_override_recomputes_without_reformatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Path(tmp) / "backend.py"
            backend.write_text(
                "\n".join(
                    [
                        "from fluent_pipeline.checksum import stamp_checksum",
                        "def recompute_checksum_bytes(data):",
                        "    return stamp_checksum(data)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            os.environ["TECAN_CHECKSUM_BACKEND"] = str(backend)
            _reset_checksum_backend()
            data = (
                b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>\r\n'
                b"<Root>\r\n  <Payload></Payload>\r\n  <Checksum></Checksum>\r\n</Root>\r\n"
            )

            with mock.patch.object(checksums, "_known_good_samples", return_value=[]):
                self.assertTrue(checksums.checksum_bridge_available())
                rewritten = checksums.recompute_checksum_bytes(data)

            self.assertIsNotNone(rewritten)
            assert rewritten is not None
            self.assertTrue(rewritten.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n  <Checksum>", rewritten)
            self.assertEqual(checksums.entry_checksum_state(rewritten), "valid")

    def test_override_must_pass_self_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = Path(tmp) / "backend.py"
            backend.write_text(
                "def recompute_checksum_bytes(data):\n    return data\n",
                encoding="utf-8",
            )
            os.environ["TECAN_CHECKSUM_BACKEND"] = str(backend)
            _reset_checksum_backend()

            with mock.patch.object(checksums, "_known_good_samples", return_value=[]):
                self.assertFalse(checksums.checksum_bridge_available())


class VendoredBackendTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("TECAN_CHECKSUM_BACKEND", None)
        _reset_checksum_backend()

    def test_vendored_backend_resolves_and_recomputes(self):
        backend = checksums._backend_from_vendored_pure_python()
        self.assertIsNotNone(backend)
        assert backend is not None
        self.assertEqual(backend.detail, "vendored-pure-python")

        entry = KNOWN_GOOD_ENTRIES[0]
        blanked = checksums._blank_checksum(entry)
        self.assertEqual(backend.recompute(blanked), entry)

    def test_self_closing_checksum_is_blank_and_recomputes(self):
        entry = b"<VxData><Payload><A>1</A></Payload><Checksum /></VxData>"
        self.assertEqual(checksums.entry_checksum_state(entry), "blank")
        backend = checksums._backend_from_vendored_pure_python()
        self.assertIsNotNone(backend)
        assert backend is not None

        rewritten = backend.recompute(entry)
        self.assertIsNotNone(rewritten)
        assert rewritten is not None
        self.assertEqual(checksums.entry_checksum_state(rewritten), "valid")
        self.assertIn(b"<Checksum>", rewritten)

    def test_duplicate_checksum_elements_are_invalid(self):
        entry = (
            b"<VxData><Payload><A>1</A></Payload>"
            b"<Checksum />"
            b"<Checksum>ABCDEF1234567890</Checksum>"
            b"</VxData>"
        )

        self.assertEqual(checksums.entry_checksum_state(entry), "invalid")

    def test_real_hex_checksum_mismatch_is_invalid(self):
        entry = (
            b"<VxData><Payload><A>1</A></Payload>"
            b"<Checksum>00000000000000000000000000000000</Checksum>"
            b"</VxData>"
        )

        self.assertEqual(checksums.entry_checksum_state(entry), "invalid")

    def test_placeholder_checksum_is_invalid(self):
        entry = (
            b"<VxData><Payload><A>1</A></Payload>"
            b"<Checksum>PLACEHOLDER</Checksum>"
            b"</VxData>"
        )

        self.assertEqual(checksums.entry_checksum_state(entry), "invalid")

    def test_vendored_backend_is_default_offline(self):
        # With no override and no real bridge importable, discovery falls back
        # to the vendored pure-Python backend and reports it as available.
        _reset_checksum_backend()
        with mock.patch.object(checksums, "_backend_from_fluentcoder", return_value=None), mock.patch.object(
            checksums, "_backend_from_fluentcontrol_core", return_value=None
        ), mock.patch.object(checksums, "_backend_from_tecan_install", return_value=None):
            self.assertTrue(checksums.checksum_bridge_available())
            self.assertTrue(checksums.checksum_backend_is_vendored())
            name = checksums.checksum_backend_name()
            self.assertIsNotNone(name)
            assert name is not None
            self.assertIn("vendored", name)

    def test_vendored_backend_fails_closed_when_self_verify_fails(self):
        with mock.patch("fluent_pipeline.checksum.verify_self", return_value=False):
            self.assertIsNone(checksums._backend_from_vendored_pure_python())

    def test_discovery_fails_closed_when_self_verify_fails(self):
        # If the vendored backend's self-verification fails and no real bridge is
        # importable, discovery selects NO backend (never a guessed value), so
        # edited entries keep blank checksums and Gate 23 blocks unless waived.
        _reset_checksum_backend()
        with mock.patch.object(
            checksums, "_backend_from_fluentcoder", return_value=None
        ), mock.patch.object(
            checksums, "_backend_from_fluentcontrol_core", return_value=None
        ), mock.patch.object(
            checksums, "_backend_from_tecan_install", return_value=None
        ), mock.patch("fluent_pipeline.checksum.verify_self", return_value=False):
            self.assertFalse(checksums.checksum_bridge_available())
            self.assertIsNone(
                checksums.recompute_checksum_bytes(
                    checksums._blank_checksum(KNOWN_GOOD_ENTRIES[0])
                )
            )

    def test_empirical_backend_is_retired(self):
        # The old empirical/brute-force backend and its helpers were removed; the
        # vendored pure-Python backend fully supersedes them.
        for name in (
            "_backend_from_empirical_samples",
            "_backend_from_empirical_payload_checksums",
            "_payload_checksum_message",
            "_matching_empirical_candidate",
            "_message_variants",
            "_remove_checksum_element",
            "_digest_candidates",
        ):
            self.assertFalse(
                hasattr(checksums, name),
                f"empirical helper {name!r} should have been removed",
            )

    def test_empirical_override_is_no_longer_special_cased(self):
        # "empirical" is no longer a recognized backend override token; it falls
        # through to module-name resolution, which fails to import -> no backend.
        os.environ["TECAN_CHECKSUM_BACKEND"] = "empirical"
        _reset_checksum_backend()
        self.assertFalse(checksums.checksum_bridge_available())

    def test_audit_reports_vendored_backend(self):
        _reset_checksum_backend()
        with mock.patch.object(checksums, "_backend_from_fluentcoder", return_value=None), mock.patch.object(
            checksums, "_backend_from_fluentcontrol_core", return_value=None
        ), mock.patch.object(checksums, "_backend_from_tecan_install", return_value=None):
            entry = checksums.recompute_checksum_bytes(
                checksums._blank_checksum(KNOWN_GOOD_ENTRIES[0])
            )
            self.assertIsNotNone(entry)
            assert entry is not None
            audit = checksums.audit_archive_checksums({"x": entry}, mutated_entries={"x"})
        self.assertEqual(audit["blank_count"], 0)
        self.assertEqual(audit["valid_count"], 1)
        self.assertTrue(audit["bridge_available"])
        self.assertTrue(audit["backend_is_vendored"])


@pytest.mark.fluentcontrol_shell
@unittest.skipUnless(
    checksums._backend_from_tecan_install() is not None,
    "FluentControl native checksum handler is required",
)
class NativeTecanChecksumTests(unittest.TestCase):
    def tearDown(self) -> None:
        _reset_checksum_backend()

    def test_redundant_namespace_script_uses_parser_checksum(self):
        source = (
            b'<VxData xmlns:i="urn:i" xmlns:d2p1="urn:array" xmlns:d3p1="urn:variables">'
            b"<Payload><PayloadData><Script>"
            b'<d2p1:anyType xmlns:d3p1="urn:variables" i:type="d3p1:Variable">'
            b"<d3p1:Name>Value</d3p1:Name></d2p1:anyType>"
            b"</Script></PayloadData></Payload><Checksum></Checksum></VxData>"
        )
        vendored = checksums._backend_from_vendored_pure_python()
        native = checksums._backend_from_tecan_install()
        self.assertIsNotNone(vendored)
        self.assertIsNotNone(native)
        assert vendored is not None and native is not None

        vendored_result = vendored.recompute(source)
        native_result = native.recompute(source)

        self.assertIsNotNone(vendored_result)
        self.assertIsNotNone(native_result)
        assert vendored_result is not None and native_result is not None
        self.assertNotEqual(
            checksums._checksum_value(vendored_result),
            checksums._checksum_value(native_result),
        )
        checksums._BRIDGE_CACHE = native
        self.assertEqual(checksums.entry_checksum_state(native_result), "valid")
        self.assertEqual(checksums.entry_checksum_state(vendored_result), "invalid")


@pytest.mark.fluentcontrol_shell
@unittest.skipUnless(
    checksums.checksum_bridge_available() and len(checksums._known_good_samples(limit=3)) >= 3,
    "verified FluentControl checksum backend and real checksum samples are required",
)
class RealChecksumRoundTripTests(unittest.TestCase):
    def test_recomputes_known_good_samples_byte_exactly(self):
        samples = checksums._known_good_samples(limit=128)
        self.assertGreaterEqual(len(samples), 3)
        covered = 0
        covered_suffixes: set[str] = set()
        covered_xml_roots: set[str] = set()

        for sample in samples:
            with self.subTest(sample=str(sample["path"])):
                blanked = checksums._blank_checksum(sample["data"])
                rewritten = checksums.recompute_checksum_bytes(blanked)
                if rewritten is None:
                    continue
                covered += 1
                covered_suffixes.add(sample["suffix"])
                if sample["suffix"] == ".xml":
                    covered_xml_roots.add(checksums._xml_root_name(sample["data"]))
                self.assertEqual(rewritten, sample["data"])
                self.assertEqual(checksums.entry_checksum_state(rewritten), "valid")
        self.assertGreaterEqual(covered, 3)
        self.assertTrue({".xcmp", ".xcon", ".xlqc", ".xscr", ".xwsp"}.issubset(covered_suffixes))
        self.assertTrue(
            {"ArchiveContent", "DirectoryMappings", "NodeDescription", "SystemInfo"}.issubset(
                covered_xml_roots
            )
        )

