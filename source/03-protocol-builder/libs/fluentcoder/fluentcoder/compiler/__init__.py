"""Compiler — renders Protocol IR to FluentControl XML."""

from .renderer import Renderer, RenderError


def render_protocol(
    protocol,
    *,
    deterministic: bool = False,
    strict_workspace_binding: bool | None = None,
) -> str:
    """Render a Protocol IR object to a `.xscr` XML string."""
    return Renderer(
        deterministic=deterministic,
        strict_workspace_binding=strict_workspace_binding,
    ).render(protocol)


__all__ = ["Renderer", "RenderError", "render_protocol"]
