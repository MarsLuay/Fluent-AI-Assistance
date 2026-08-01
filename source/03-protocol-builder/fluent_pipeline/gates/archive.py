"""Archive-input readiness evaluators."""

from __future__ import annotations

import zipfile

from tecan_common.zeia_limits import validate_zeia_archive_limits

from .models import GateRecord, ValidationContext


def evaluate_zeia_parsed(context: ValidationContext) -> GateRecord:
    """Check the imported source manifest or supplied source ZEIA archives."""
    source_manifest = context.source_manifest
    errors = source_manifest.get("errors") if isinstance(source_manifest, dict) else None
    if source_manifest is not None and not errors:
        return context.make_gate(
            "zeia_parsed",
            "passed",
            "Source ZEIA manifest is available and has no import errors.",
        )
    if errors:
        return context.make_gate(
            "zeia_parsed",
            "failed",
            "Source ZEIA manifest contains import errors.",
            {"errors": errors},
        )
    zeia_paths = [path for path in context.source_projects if path.suffix.lower() == ".zeia"]
    if zeia_paths:
        try:
            for path in zeia_paths:
                if not path.exists() or not zipfile.is_zipfile(path):
                    return context.make_gate(
                        "zeia_parsed",
                        "failed",
                        "No parsed ZEIA manifest or readable source ZEIA archive was provided.",
                    )
                with zipfile.ZipFile(path) as archive:
                    validate_zeia_archive_limits(archive)
        except zipfile.BadZipFile as exc:
            return context.make_gate(
                "zeia_parsed",
                "failed",
                "Source ZEIA archive exceeds the safe import limits.",
                {"error": str(exc)},
            )
        return context.make_gate(
            "zeia_parsed",
            "passed",
            "Source ZEIA archive(s) are readable zip files within the safe import limits.",
        )
    return context.make_gate(
        "zeia_parsed",
        "failed",
        "No parsed ZEIA manifest or readable source ZEIA archive was provided.",
    )
