from __future__ import annotations

from pathlib import Path
from unittest import mock
import tempfile
import zipfile

import pytest

from tecan_reader.diagnostics import DiagnosticCode, IngestionArchiveError
from tecan_reader.zeia_adapters import ingest_zeia, probe_zeia


SCRIPT = b'''<?xml version="1.0"?>
<Root><ObjectName>Bounded</ObjectName><Script version="1.0" /></Root>
'''


def _archive(root: Path, name: str, entries: list[tuple[str, bytes]], *, compression: int = zipfile.ZIP_STORED) -> Path:
    path = root / name
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for entry, payload in entries:
            archive.writestr(entry, payload)
    return path


def test_per_member_limit_is_structured_before_member_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = _archive(Path(tmp), "oversized.zeia", [("Scripts/main.xscr", b"x" * 128)])

        with pytest.raises(IngestionArchiveError) as raised:
            probe_zeia(archive, max_member_uncompressed_bytes=64)

    diagnostic = raised.value.diagnostic
    assert diagnostic["code"] == DiagnosticCode.LIMIT_MEMBER_BYTES
    assert diagnostic["entry_path"] == "Scripts/main.xscr"
    assert diagnostic["compressed_size"] == 128
    assert diagnostic["uncompressed_size"] == 128
    assert diagnostic["limit"] == 64
    assert diagnostic["violated_limit"] == "max_member_uncompressed_bytes"


def test_oversized_object_member_is_rejected_before_member_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = _archive(Path(tmp), "oversized-object.zeia", [("Objects/main.xcmp", b"x" * 128)])

        with pytest.raises(IngestionArchiveError) as raised:
            probe_zeia(archive, max_member_uncompressed_bytes=64)

    diagnostic = raised.value.diagnostic
    assert diagnostic["code"] == DiagnosticCode.LIMIT_MEMBER_BYTES
    assert diagnostic["entry_path"] == "Objects/main.xcmp"
    assert diagnostic["uncompressed_size"] == 128


def test_large_text_member_uses_bounded_stream_read_instead_of_zipfile_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        payload = b"<Root><ObjectName>Bounded</ObjectName><Script />" + b"x" * (4 * 1024 * 1024) + b"</Root>"
        archive = _archive(Path(tmp), "large.zeia", [("Scripts/main.xscr", payload)])

        with mock.patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("whole-member read")):
            model = ingest_zeia(archive, max_compression_ratio=None)

    assert model.scripts[0]["source_metadata"]["oversized_xml"] is True
    assert "Scripts/main.xscr" in model.completeness["oversized_members"]


def test_duplicate_windows_and_posix_names_are_rejected_after_normalization() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = _archive(
            Path(tmp),
            "duplicate.zeia",
            [("DataStore/UserSpecific/main.xscr", SCRIPT), (r"DataStore\\UserSpecific\\main.xscr", SCRIPT)],
        )

        with pytest.raises(IngestionArchiveError) as raised:
            probe_zeia(archive, max_compression_ratio=None)

    diagnostic = raised.value.diagnostic
    assert diagnostic["code"] == DiagnosticCode.ARCHIVE_DUPLICATE_MEMBER
    assert diagnostic["entry_path"] == "DataStore/UserSpecific/main.xscr"
    assert "compressed_size" in diagnostic
    assert "uncompressed_size" in diagnostic


def test_compression_ratio_guard_is_configurable_and_distinct_from_size_limit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = _archive(
            Path(tmp),
            "ratio.zeia",
            [("Scripts/main.xscr", b"x" * 64 * 1024)],
            compression=zipfile.ZIP_DEFLATED,
        )

        with pytest.raises(IngestionArchiveError) as raised:
            probe_zeia(
                archive,
                max_member_uncompressed_bytes=None,
                max_compression_ratio=2.0,
            )

    assert raised.value.diagnostic["code"] == DiagnosticCode.LIMIT_COMPRESSION_RATIO
    assert raised.value.diagnostic["violated_limit"] == "max_compression_ratio"
    assert raised.value.diagnostic["compressed_size"] < raised.value.diagnostic["uncompressed_size"]


def test_unicode_member_names_are_preserved_in_the_validated_inventory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = _archive(
            Path(tmp),
            "unicode.zeia",
            [("DataStore/ÜserSpecific/mañana.xscr", SCRIPT)],
        )

        result = probe_zeia(archive, max_compression_ratio=None)

    assert result.entries == ("DataStore/ÜserSpecific/mañana.xscr",)
