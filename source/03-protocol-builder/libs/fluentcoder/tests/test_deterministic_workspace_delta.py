"""Deterministic WorkspaceDelta GUID rendering."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.compiler import render_protocol  # noqa: E402
from fluentcoder.compiler.renderer import Renderer  # noqa: E402
from fluentcoder.ir.schema import CommentStep, Group, Protocol  # noqa: E402
from fluentcoder.worktable import Worktable  # noqa: E402

_GUID_RE = re.compile(
    r"(&lt;Identifier&gt;)[0-9a-f-]{36}(&lt;/Identifier&gt;)"
)


def _minimal_protocol() -> Protocol:
    return Protocol(
        name="Deterministic WorkspaceDelta Test",
        worktable_guid="00000000-0000-0000-0000-000000000001",
        worktable_name="WT",
        groups=[Group(name="Main", steps=[CommentStep(comment="noop")])],
    )


def test_deterministic_renderer_produces_identical_xml() -> None:
    protocol = _minimal_protocol()
    xml_a = Renderer(deterministic=True).render(protocol)
    xml_b = Renderer(deterministic=True).render(protocol)
    assert xml_a == xml_b


def test_deterministic_render_protocol_produces_identical_xml() -> None:
    protocol = _minimal_protocol()
    xml_a = render_protocol(protocol, deterministic=True)
    xml_b = render_protocol(protocol, deterministic=True)
    assert xml_a == xml_b


def test_deterministic_compile_produces_identical_xscr(tmp_path: Path) -> None:
    wt = Worktable(name="Deterministic WorkspaceDelta Test")
    wt.workspace_guid = "00000000-0000-0000-0000-000000000001"
    wt.workspace_name = "WT"
    wt.group("Main")
    wt.add_comment("noop")

    out_a = tmp_path / "a.xscr"
    out_b = tmp_path / "b.xscr"
    wt.compile(out_a, deterministic=True)
    wt.compile(out_b, deterministic=True)

    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")


def test_default_renderer_still_uses_random_guid() -> None:
    protocol = _minimal_protocol()
    xml_a = Renderer().render(protocol)
    xml_b = Renderer().render(protocol)
    guid_a = _GUID_RE.search(xml_a)
    guid_b = _GUID_RE.search(xml_b)
    assert guid_a is not None
    assert guid_b is not None
    assert guid_a.group(0) != guid_b.group(0)

