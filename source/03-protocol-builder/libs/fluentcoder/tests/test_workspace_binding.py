"""Workspace binding: explicit protocol fields vs generation.yaml fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.compiler import RenderError, render_protocol  # noqa: E402
from fluentcoder.compiler.renderer import Renderer  # noqa: E402
from fluentcoder.ir.schema import CommentStep, Group, Protocol  # noqa: E402
from fluentcoder.worktable import Worktable  # noqa: E402


def _minimal_protocol(*, worktable_guid: str | None = None, worktable_name: str | None = None) -> Protocol:
    return Protocol(
        name="Workspace Binding Test",
        worktable_guid=worktable_guid,
        worktable_name=worktable_name,
        groups=[Group(name="Main", steps=[CommentStep(comment="noop")])],
    )


def _bound_worktable() -> Worktable:
    wt = Worktable(name="Bound compile test")
    wt.workspace_name = "Explicit WS"
    wt.workspace_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    wt.group("Main")
    wt.add_comment("ready")
    return wt


def test_render_uses_explicit_protocol_worktable() -> None:
    protocol = _minimal_protocol(
        worktable_guid="11111111-2222-3333-4444-555555555555",
        worktable_name="My Workspace",
    )
    xml = render_protocol(protocol)
    assert "11111111-2222-3333-4444-555555555555" in xml
    assert "<ObjectName>My Workspace</ObjectName>" in xml


def test_render_without_binding_strict_raises() -> None:
    protocol = _minimal_protocol()
    with pytest.raises(RenderError, match="Strict workspace binding is enabled"):
        Renderer(strict_workspace_binding=True).render(protocol)


def test_render_without_binding_fails_when_generation_yaml_worktable_empty() -> None:
    """Shipped generation.yaml leaves worktable empty — fail closed, no 780_Empty invent."""
    protocol = _minimal_protocol()
    renderer = Renderer()
    config_wt = renderer.config.get("worktable") or {}
    assert not str(config_wt.get("guid") or "").strip()
    assert not str(config_wt.get("name") or "").strip()

    with pytest.raises(RenderError, match="provides no worktable fallback"):
        renderer.render(protocol)


def test_render_uses_site_overlay_worktable_fallback(tmp_path: Path) -> None:
    """Site/CI overlays may still provide a local worktable fallback (warns)."""
    import yaml

    overlay = tmp_path / "generation.yaml"
    base = Path(__file__).resolve().parent.parent / "fluentcoder" / "_assets" / "config" / "generation.yaml"
    cfg = yaml.safe_load(base.read_text(encoding="utf-8"))
    cfg["worktable"] = {
        "guid": "ffffffff-1111-2222-3333-444444444444",
        "name": "Site_Overlay_WS",
        "type_id": "WorktableWorkspace",
    }
    overlay.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    protocol = _minimal_protocol()
    renderer = Renderer(config_path=overlay)
    with pytest.warns(UserWarning, match="generation.yaml default"):
        xml = renderer.render(protocol)
    assert "ffffffff-1111-2222-3333-444444444444" in xml
    assert "<ObjectName>Site_Overlay_WS</ObjectName>" in xml


def test_compile_with_bound_worktable(tmp_path: Path) -> None:
    wt = _bound_worktable()
    proto = wt.to_protocol()
    assert proto.worktable_guid == wt.workspace_guid
    assert proto.worktable_name == wt.workspace_name

    out = wt.compile(tmp_path / "bound.xscr")
    text = out.read_text(encoding="utf-8")
    assert wt.workspace_guid in text
    assert f"<ObjectName>{wt.workspace_name}</ObjectName>" in text


def test_compile_without_binding_raises(tmp_path: Path) -> None:
    wt = Worktable(name="Unbound compile test")
    wt.group("Main")
    wt.add_comment("no workspace")

    with pytest.raises(ValueError, match="not bound to a specific FluentControl workspace"):
        wt.compile(tmp_path / "unbound.xscr")

