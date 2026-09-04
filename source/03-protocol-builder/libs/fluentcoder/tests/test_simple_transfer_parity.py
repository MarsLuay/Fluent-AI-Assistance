"""v1 acceptance: byte-equal parity render against a pinned golden fixture.

Compiles the fluentcoder simple_transfer protocol and compares normalized XML
to ``tests/fixtures/simple_transfer_expected.xscr``. Normalization collapses
environment-specific workspace metadata, WorkspaceDelta GUIDs, and checksums so
the test runs fully offline with the synthetic catalog fixture.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_FIXTURE = REPO_ROOT / "examples" / "simple_transfer.py"
EXPECTED_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "simple_transfer_expected.xscr"


from tests._module_loader import load_module  # noqa: E402

_WORKSPACE_GUID_RE = re.compile(
    r"&lt;Identifier&gt;[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}&lt;/Identifier&gt;"
)
_WORKTABLE_REF_RE = re.compile(
    r"(<Reference>\s*<Guid>)[^<]+(</Guid>\s*<TypeId>WorktableWorkspace</TypeId>\s*<ObjectName>)[^<]+(</ObjectName>\s*</Reference>)"
)
_BASE_WORKSPACE_RE = re.compile(
    r"(<BaseWorkspaceName>)[^<]+(</BaseWorkspaceName>)"
)
_CHECKSUM_RE = re.compile(r"<Checksum>[0-9A-F]+</Checksum>")
_TECAN_USB_SERIAL_RE = re.compile(r"USB:TECAN,FLUENT,[0-9]+/([A-Z0-9]+:1)")


def _normalize_xml(xml: str) -> str:
    """Collapse environment-specific workspace metadata for parity comparison."""
    xml = _WORKSPACE_GUID_RE.sub(
        "&lt;Identifier&gt;<NORMALIZED>&lt;/Identifier&gt;",
        xml,
    )
    xml = _WORKTABLE_REF_RE.sub(
        r"\1<NORMALIZED>\2<NORMALIZED>\3",
        xml,
    )
    xml = _BASE_WORKSPACE_RE.sub(r"\1<NORMALIZED>\2", xml)
    xml = _CHECKSUM_RE.sub("<Checksum>NORMALIZED</Checksum>", xml)
    xml = _TECAN_USB_SERIAL_RE.sub(r"USB:TECAN,FLUENT,<NORMALIZED>/\1", xml)
    return xml


def _compile_protocol(protocol_path: Path) -> str:
    """Compile a protocol fixture to its `.xscr` XML string."""
    module = load_module(protocol_path, alias="simple_transfer_parity")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "simple_transfer.xscr"
        module.build_worktable().compile(out, deterministic=True)
        return out.read_text(encoding="utf-8")


@pytest.mark.usefixtures("synthetic_catalog")
def test_simple_transfer_parity_xml() -> None:
    """fluentcoder simple_transfer renders XML matching the pinned golden fixture."""
    if not PROTOCOL_FIXTURE.exists():
        pytest.fail(f"missing protocol fixture: {PROTOCOL_FIXTURE}")
    if not EXPECTED_FIXTURE.exists():
        pytest.fail(f"missing expected fixture: {EXPECTED_FIXTURE}")

    actual_xml = _compile_protocol(PROTOCOL_FIXTURE)
    expected_xml = EXPECTED_FIXTURE.read_text(encoding="utf-8")

    assert _normalize_xml(actual_xml) == _normalize_xml(expected_xml), (
        "compiled simple_transfer XML differs from pinned golden fixture "
        f"(actual len={len(actual_xml)}, expected len={len(expected_xml)})"
    )


@pytest.mark.usefixtures("synthetic_catalog")
def test_simple_transfer_protocol_ir_shape() -> None:
    """Sanity: the protocol has the right groups and step types."""
    module = load_module(PROTOCOL_FIXTURE, alias="simple_transfer_ir_shape")
    proto = module.build_worktable().to_protocol()
    assert proto.name == "Simple transfer"
    assert [g.name for g in proto.groups] == ["Setup", "Transfer"]
    setup_types = [type(s).__name__ for s in proto.groups[0].steps]
    transfer_types = [type(s).__name__ for s in proto.groups[1].steps]
    assert setup_types == ["AddLabwareStep"] * 3
    assert transfer_types == [
        "GetHeadAdapterStep",
        "PickUpTipsStep",
        "AspirateStep",
        "DispenseStep",
        "SetTipsBackStep",
        "DropHeadAdapterStep",
    ]

