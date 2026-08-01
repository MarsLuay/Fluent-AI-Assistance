"""Local protocol template discovery for reusable IR shapes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import PROJECT_DIR
from .protocol_ir import load_protocol_ir
from .protocol_ir_schema import validate_protocol_ir_document


TEMPLATES_DIR = PROJECT_DIR / "templates"


def list_templates(templates_dir: Path = TEMPLATES_DIR) -> list[dict[str, Any]]:
    """Return installed template summaries."""
    if not templates_dir.exists():
        return []
    items = []
    for path in sorted(templates_dir.iterdir()):
        if not path.is_dir():
            continue
        template_ir = path / "template.ir.json"
        request_schema = path / "request.schema.json"
        if not template_ir.exists() or not request_schema.exists():
            continue
        try:
            ir = load_protocol_ir(template_ir)
        except Exception:
            ir = {}
        template_meta = ir.get("template") if isinstance(ir.get("template"), dict) else {}
        protocol = ir.get("protocol") if isinstance(ir.get("protocol"), dict) else {}
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "template_ir": str(template_ir),
                "request_schema": str(request_schema),
                "description": template_meta.get("description") or protocol.get("comment") or "",
                "step_count": len(ir.get("steps") or []),
            }
        )
    return items


def template_path(name: str, templates_dir: Path = TEMPLATES_DIR) -> Path:
    """Resolve a template folder by name."""
    clean = name.strip()
    if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise ValueError(f"Invalid template name: {name!r}")
    path = templates_dir / clean
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {clean}")
    return path


def load_template_ir(name: str, templates_dir: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Load and validate a template's canonical IR."""
    return load_protocol_ir(template_path(name, templates_dir) / "template.ir.json")


def load_request_schema(name: str, templates_dir: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Load the template-specific request schema."""
    path = template_path(name, templates_dir) / "request.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def template_info(name: str, templates_dir: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Return a detailed template inventory record."""
    folder = template_path(name, templates_dir)
    ir = load_template_ir(name, templates_dir)
    schema = load_request_schema(name, templates_dir)
    examples = sorted(str(path) for path in (folder / "examples").glob("*") if path.is_file())
    issues = [
        issue.as_dict() if hasattr(issue, "as_dict") else dict(issue)
        for issue in validate_protocol_ir_document(ir)
    ]
    return {
        "name": folder.name,
        "path": str(folder),
        "template_ir": str(folder / "template.ir.json"),
        "request_schema": str(folder / "request.schema.json"),
        "schema_id": schema.get("$id", ""),
        "protocol_name": (ir.get("protocol") or {}).get("name"),
        "description": (ir.get("template") or {}).get("description") or (ir.get("protocol") or {}).get("comment", ""),
        "step_count": len(ir.get("steps") or []),
        "labware_count": len(ir.get("labware") or []),
        "examples": examples,
        "valid": not [issue for issue in issues if issue.get("severity") == "error"],
        "issues": issues,
    }
