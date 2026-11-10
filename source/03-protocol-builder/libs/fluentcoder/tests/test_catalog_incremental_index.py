"""Incremental catalog indexing tests."""

from __future__ import annotations

import math
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

# Avoid the package-level ensure_index() trying to rebuild against a real FC install.
os.environ.setdefault("FLUENTCODER_NO_AUTO_REBUILD", "1")
os.environ.setdefault("FLUENTCODER_FC_INSTALL", str(Path(__file__).resolve().parent / "_missing_fc_install"))

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder import xml_compat as ET  # noqa: E402
from tecan_common import native_acceleration  # noqa: E402

from fluentcoder.catalog.indexer import (  # noqa: E402
    _source_content_fingerprint,
    build_index,
    fingerprint_matches,
)
from fluentcoder.catalog.xcmp import (  # noqa: E402
    XcmpArrangement,
    XcmpComponent,
    XwspWorkspace,
    _direct_child,
    _direct_children,
    _find,
    _findall,
    _float_text,
    _int_text,
    _local,
    _text,
)


def _make_install(root: Path, *, component_text: str = "<Component/>") -> Path:
    install = root / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)
    (components / "deck.xcmp").write_text(component_text, encoding="utf-8")
    (workspaces / "layout.xwsp").write_text("<Workspace/>", encoding="utf-8")
    return install


def _arrangement() -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=1,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
    )


def _fake_workspace(path: Path) -> XwspWorkspace:
    return XwspWorkspace(guid=f"{path.stem}-guid", name=path.stem.title(), file_path=path)


class CatalogIncrementalIndexTests(unittest.TestCase):
    def test_catalog_xml_helpers_match_native_and_python_paths(self):
        root = ET.Element("{urn:synthetic}Root")
        root.append(ET.Comment("ignored"))
        first = ET.SubElement(root, "{urn:synthetic}Item")
        first.text = " 42 \t"
        group = ET.SubElement(root, "Group")
        nested = ET.SubElement(group, "Item")
        second = ET.SubElement(root, "Item")
        integer = ET.SubElement(root, "Integer")
        integer.text = "١٢"
        large_integer = ET.SubElement(root, "LargeInteger")
        large_integer.text = "9223372036854775808"
        decimal = ET.SubElement(root, "Decimal")
        decimal.text = "1.25e2"
        negative_zero = ET.SubElement(root, "NegativeZero")
        negative_zero.text = "-0.0"
        nan = ET.SubElement(root, "Nan")
        nan.text = "nan"

        def collect():
            return {
                "local": _local(first.tag),
                "first": _find(root, "Item"),
                "all": _findall(root, "Item"),
                "direct": _direct_children(root, "Item"),
                "direct_first": _direct_child(root, "Item"),
                "text": _text(first),
                "unicode_integer": _int_text(integer),
                "large_integer": _int_text(large_integer),
                "decimal": _float_text(decimal),
                "negative_zero": _float_text(negative_zero),
            }

        with mock.patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = collect()
            expected_nan = _float_text(nan)
        with mock.patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with ExitStack() as stack:
                calls = {
                    name: stack.enter_context(
                        mock.patch.object(
                            native_acceleration,
                            name,
                            wraps=getattr(native_acceleration, name),
                        )
                    )
                    for name in (
                        "xml_local_name",
                        "xml_first_element_by_name",
                        "xml_elements_by_name",
                        "xml_direct_elements_by_name",
                        "xml_first_direct_element_by_name",
                        "xml_clean_text",
                        "parse_i64_text",
                        "parse_f64_decimal_text",
                    )
                }
                actual = collect()
                actual_nan = _float_text(nan)
                for name, call in calls.items():
                    self.assertGreater(call.call_count, 0, name)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["all"], [first, nested, second])
        self.assertEqual(actual["direct"], [first, second])
        self.assertIs(actual["first"], first)
        self.assertIs(actual["direct_first"], first)
        self.assertEqual(actual["text"], "42")
        self.assertEqual(actual["unicode_integer"], 12)
        self.assertEqual(actual["large_integer"], 9223372036854775808)
        self.assertEqual(actual["decimal"], 125.0)
        self.assertEqual(math.copysign(1.0, actual["negative_zero"]), -1.0)
        self.assertEqual(math.copysign(1.0, expected["negative_zero"]), -1.0)
        self.assertTrue(math.isnan(expected_nan))
        self.assertTrue(math.isnan(actual_nan))

    def test_stat_fingerprint_ignores_mtime_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp))
            db_path = Path(tmp) / "index.db"
            with mock.patch("fluentcoder.catalog.indexer.load_xcmp") as load_xcmp, mock.patch(
                "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
            ):
                load_xcmp.side_effect = lambda path: XcmpComponent(
                    guid=f"{path.stem}-guid",
                    name=path.stem.title(),
                    file_path=path,
                    arrangement=_arrangement(),
                )
                build_index(install_path=install, db_path=db_path)
                self.assertTrue(fingerprint_matches(install, db_path))

                future = time.time() + 120
                for path in install.rglob("*"):
                    if path.is_file():
                        os.utime(path, (future, future))

                self.assertTrue(fingerprint_matches(install, db_path))

    def test_build_index_skips_unchanged_component_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp))
            db_path = Path(tmp) / "index.db"
            load_calls = 0

            def counting_load_xcmp(path: Path) -> XcmpComponent:
                nonlocal load_calls
                load_calls += 1
                return XcmpComponent(
                    guid=f"{path.stem}-guid",
                    name=path.stem.title(),
                    file_path=path,
                    arrangement=_arrangement(),
                )

            with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=counting_load_xcmp), mock.patch(
                "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
            ):
                first = build_index(install_path=install, db_path=db_path)
                self.assertEqual(first["components"], 1)
                self.assertEqual(load_calls, 1)

                load_calls = 0
                second = build_index(install_path=install, db_path=db_path)
                self.assertEqual(second["components"], 1)
                self.assertEqual(load_calls, 0)

    def test_build_index_reparses_changed_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp))
            db_path = Path(tmp) / "index.db"
            load_calls = 0

            def counting_load_xcmp(path: Path) -> XcmpComponent:
                nonlocal load_calls
                load_calls += 1
                return XcmpComponent(
                    guid=f"{path.stem}-guid",
                    name=path.stem.title(),
                    file_path=path,
                    arrangement=_arrangement(),
                )

            with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=counting_load_xcmp), mock.patch(
                "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
            ):
                build_index(install_path=install, db_path=db_path)
                self.assertEqual(load_calls, 1)

                component_path = install / "SystemSpecific" / "Worktable" / "Components" / "deck.xcmp"
                component_path.write_text("<Component changed='yes'/>", encoding="utf-8")

                load_calls = 0
                build_index(install_path=install, db_path=db_path)
                self.assertEqual(load_calls, 1)

    def test_content_fingerprint_changes_with_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp), component_text="<Component v='1'/>")
            path = install / "SystemSpecific" / "Worktable" / "Components" / "deck.xcmp"
            first = _source_content_fingerprint(path, install)
            path.write_text("<Component v='2'/>", encoding="utf-8")
            second = _source_content_fingerprint(path, install)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()

