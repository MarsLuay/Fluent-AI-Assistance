from __future__ import annotations

from unittest.mock import patch

import fluent_pipeline.zeia_filesystem as zeia_filesystem
from tecan_common import native_acceleration


def test_archive_path_text_uses_native_whitespace_collapse_and_fallback() -> None:
    file_reference_xml = (
        "<FileReference><File> \tAlpha\nBeta\u2003 </File></FileReference>"
    )
    script_xml = (
        "<Root><SelectedImagePath> \tAlpha\nBeta\u2003 </SelectedImagePath></Root>"
    ).encode("utf-8")
    archive_data = {"sample.xscr": script_xml}

    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
        expected_paths = zeia_filesystem._extract_file_reference_paths(
            file_reference_xml
        )
        expected_findings = zeia_filesystem.audit_archive_filesystem(archive_data)
    assert expected_paths == ["Alpha Beta"]
    assert expected_findings == []

    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            zeia_filesystem,
            "_native_collapse_ascii_whitespace",
            wraps=native_acceleration.collapse_ascii_whitespace,
        ) as native_call:
            actual_paths = zeia_filesystem._extract_file_reference_paths(
                file_reference_xml
            )
            actual_findings = zeia_filesystem.audit_archive_filesystem(archive_data)
            assert native_call.call_count == 2
    assert actual_paths == expected_paths
    assert actual_findings == expected_findings

    with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
        with patch.object(
            zeia_filesystem,
            "_native_collapse_ascii_whitespace",
            return_value=None,
        ):
            fallback_paths = zeia_filesystem._extract_file_reference_paths(
                file_reference_xml
            )
            fallback_findings = zeia_filesystem.audit_archive_filesystem(archive_data)
    assert fallback_paths == expected_paths
    assert fallback_findings == expected_findings
