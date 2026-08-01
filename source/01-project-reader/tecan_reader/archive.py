"""Reader for `.zeia` FluentControl export archives."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import zipfile

from .common import extension_counts
from .gwl import inspect_gwl_lines
from .script import inspect_xscr_text
from .xmlobj import inspect_xml_object_text
from tecan_common.zeia_limits import (
    MAX_ZEIA_ENTRY_COUNT,
    MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
    validate_zeia_archive_limits,
)


XML_OBJECT_EXTS = {".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"}
ASSET_EXTS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}


def inspect_archive(
    path: str | Path,
    *,
    script_limit: int | None = 50,
    object_limit: int | None = 200,
    max_entry_count: int = MAX_ZEIA_ENTRY_COUNT,
    max_total_uncompressed_bytes: int = MAX_ZEIA_TOTAL_UNCOMPRESSED_BYTES,
) -> dict[str, Any]:
    archive_path = Path(path)
    with zipfile.ZipFile(archive_path) as zf:
        infos = validate_zeia_archive_limits(
            zf,
            max_entry_count=max_entry_count,
            max_total_uncompressed_bytes=max_total_uncompressed_bytes,
        )
        names = [info.filename for info in infos]
        scripts = []
        objects = []
        gwls = []
        errors = []
        for name in names:
            suffix = Path(name).suffix.lower()
            if suffix == ".xscr":
                if script_limit is not None and len(scripts) >= script_limit:
                    continue
                try:
                    text = zf.read(name).decode("utf-8-sig")
                    scripts.append(inspect_xscr_text(text, source_name=name))
                except Exception as exc:
                    errors.append({"entry": name, "error": str(exc)})
            elif suffix == ".gwl":
                try:
                    text = zf.read(name).decode("utf-8-sig")
                    gwls.append(inspect_gwl_lines(text.splitlines(), source_name=name))
                except Exception as exc:
                    errors.append({"entry": name, "error": str(exc)})
            elif suffix in XML_OBJECT_EXTS:
                if object_limit is not None and len(objects) >= object_limit:
                    continue
                try:
                    text = zf.read(name).decode("utf-8-sig")
                    objects.append(inspect_xml_object_text(text, source_name=name, suffix=suffix))
                except Exception:
                    # Many metadata XML files are not useful for object indexing.
                    continue
            elif suffix in ASSET_EXTS:
                if object_limit is not None and len(objects) >= object_limit:
                    continue
                objects.append(
                    {
                        "kind": "asset",
                        "source": name,
                        "object_name": Path(name).name,
                        "type_id": suffix.lstrip("."),
                        "functional_group": "asset",
                        "footprint": "",
                        "renderer": "",
                        "names": [Path(name).name],
                        "guids": [],
                        "asset_refs": [Path(name).name],
                        "pin_refs": [],
                        "custom_part": True,
                    }
                )

    script_names = [script.get("object_name") or script.get("source") for script in scripts]
    family_counts: Counter[str] = Counter()
    command_counts: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    for script in scripts:
        family_counts.update(script.get("family_counts", {}))
        command_counts.update(script.get("command_counts", {}))
        warnings.update(script.get("warnings", []))

    return {
        "kind": "zeia",
        "source": str(archive_path),
        "entry_count": len(names),
        "extension_counts": extension_counts(names),
        "script_count_total": sum(1 for name in names if Path(name).suffix.lower() == ".xscr"),
        "script_count_summarized": len(scripts),
        "object_count_summarized": len(objects),
        "gwl_count_summarized": len(gwls),
        "script_names": script_names,
        "family_counts": dict(family_counts.most_common()),
        "command_counts": dict(command_counts.most_common(40)),
        "warning_counts": dict(warnings.most_common()),
        "scripts": scripts,
        "objects": objects,
        "gwls": gwls,
        "errors": errors,
    }
