"""Byte-backed provenance for source-preserved FluentControl expressions."""

from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from fluentcoder.expressions import (
    canonical_expression_command_id,
    canonical_expression_key,
    extract_opaque_expression_references,
    expression_inventory_from_xscr_text,
    registered_expression_field_paths,
    try_parse_expression,
)


EXPRESSION_PROVENANCE_SCHEMA_VERSION = "tecan.expression_provenance.v1"
EXPRESSION_PROVENANCE_FILENAME = "expression_provenance.json"


def build_expression_provenance_ledger(source_artifacts: Iterable[Path]) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for raw_path in source_artifacts:
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if resolved in seen_paths or not path.is_file():
            continue
        seen_paths.add(resolved)
        source, source_records = _scan_source_artifact(path)
        sources.append(source)
        records.extend(source_records)
    records.sort(key=lambda item: str(item["record_id"]))
    sources.sort(key=lambda item: (str(item["sha256"]), str(item["kind"]), str(item["name"])))
    payload = {
        "schema_version": EXPRESSION_PROVENANCE_SCHEMA_VERSION,
        "source_artifacts": sources,
        "records": records,
    }
    payload["ledger_sha256"] = _ledger_sha256(payload)
    return payload


def write_expression_provenance_ledger(path: Path, ledger: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(ledger), indent=2, sort_keys=True), encoding="utf-8")


def load_expression_provenance_ledger(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def verify_expression_provenance_ledger(
    ledger: Mapping[str, Any] | None,
    source_artifacts: Iterable[Path],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(ledger, Mapping):
        return {"valid": False, "errors": [{"reason": "missing_ledger"}], "records": {}}
    if ledger.get("schema_version") != EXPRESSION_PROVENANCE_SCHEMA_VERSION:
        errors.append({"reason": "unsupported_schema_version"})
    expected_ledger_hash = _ledger_sha256(ledger)
    if str(ledger.get("ledger_sha256") or "") != expected_ledger_hash:
        errors.append({"reason": "ledger_hash_mismatch"})

    rebuilt = build_expression_provenance_ledger(source_artifacts)
    if _source_artifact_identity_counts(ledger) != _source_artifact_identity_counts(rebuilt):
        errors.append({"reason": "source_artifact_inventory_mismatch"})
    rebuilt_records = {
        str(item.get("record_id") or ""): item
        for item in rebuilt.get("records") or []
        if isinstance(item, dict) and item.get("record_id")
    }
    ledger_record_ids = [
        str(item.get("record_id") or "")
        for item in ledger.get("records") or []
        if isinstance(item, Mapping)
    ]
    if (
        len(ledger_record_ids) != len(set(ledger_record_ids))
        or set(ledger_record_ids) != set(rebuilt_records)
    ):
        errors.append({"reason": "record_inventory_mismatch"})
    trusted: dict[str, dict[str, Any]] = {}
    for record in ledger.get("records") or []:
        if not isinstance(record, Mapping):
            errors.append({"reason": "invalid_record"})
            continue
        record_id = str(record.get("record_id") or "")
        actual = rebuilt_records.get(record_id)
        if not record_id or actual is None:
            errors.append({"reason": "record_not_found_in_source", "record_id": record_id})
            continue
        if _record_identity(record) != _record_identity(actual):
            errors.append({"reason": "record_identity_mismatch", "record_id": record_id})
            continue
        trusted[record_id] = dict(record)
    return {
        "valid": not errors,
        "errors": errors,
        "records": trusted if not errors else {},
        "source_artifact_count": len(rebuilt.get("source_artifacts") or []),
        "record_count": len(trusted) if not errors else 0,
    }


def bind_protocol_ir_expression_provenance(
    protocol_ir: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    bound = copy.deepcopy(dict(protocol_ir))
    records = [
        item
        for item in ledger.get("records") or []
        if isinstance(item, Mapping) and item.get("record_id")
    ]
    protocols = bound.get("protocols") if isinstance(bound.get("protocols"), list) else [bound]
    for protocol in protocols:
        if isinstance(protocol, dict):
            _bind_protocol(protocol, records)
    return bound


def source_preserved_expression_allowlist_from_bundle(bundle_root: Path) -> list[dict[str, Any]]:
    return source_preserved_expression_context_from_bundle(bundle_root)["allowlist"]


def source_preserved_expression_context_from_bundle(bundle_root: Path) -> dict[str, Any]:
    protocol_ir = _load_bundle_protocol_ir(bundle_root)
    ledger = _load_bundle_expression_provenance(bundle_root)
    source_artifacts = _bundle_source_artifacts(bundle_root)
    ledger_verification = verify_expression_provenance_ledger(
        ledger,
        source_artifacts,
    )
    verification = verify_protocol_ir_expression_provenance(
        protocol_ir,
        ledger_verification,
    )
    return {
        "verification": verification,
        "allowlist": source_preserved_expression_allowlist_from_verified_ledger(
            protocol_ir,
            verification,
        ),
    }


def verify_protocol_ir_expression_provenance(
    protocol_ir: Mapping[str, Any] | None,
    ledger_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    verification = dict(ledger_verification or {})
    errors = list(verification.get("errors") or [])
    if not verification.get("valid"):
        verification["valid"] = False
        verification["errors"] = errors
        verification["bound_expression_count"] = 0
        return verification
    trusted = verification.get("records")
    if not isinstance(trusted, Mapping):
        errors.append({"reason": "missing_verified_records"})
        verification["valid"] = False
        verification["errors"] = errors
        verification["bound_expression_count"] = 0
        return verification

    occurrences = (
        _source_preserved_ir_occurrences(protocol_ir)
        if isinstance(protocol_ir, Mapping)
        else []
    )
    provenance_counts = Counter(
        str(item["expression"].get("provenance_id") or "")
        for item in occurrences
    )
    bound_count = 0
    for occurrence in occurrences:
        expression = occurrence["expression"]
        provenance_id = str(expression.get("provenance_id") or "")
        error_context = {
            "provenance_id": provenance_id,
            "command": occurrence.get("command"),
            "field": occurrence.get("field"),
            "line_number": occurrence.get("line_number"),
            "command_index": occurrence.get("command_index"),
        }
        if not provenance_id:
            errors.append({"reason": "missing_provenance_id", **error_context})
            continue
        if provenance_counts[provenance_id] != 1:
            errors.append(
                {"reason": "provenance_id_not_unique_in_protocol_ir", **error_context}
            )
            continue
        source_record = trusted.get(provenance_id)
        if not isinstance(source_record, Mapping):
            errors.append(
                {"reason": "provenance_record_not_verified", **error_context}
            )
            continue
        if not _expression_matches_verified_record(occurrence, source_record):
            errors.append(
                {"reason": "expression_metadata_mismatch", **error_context}
            )
            continue
        bound_count += 1

    verification["valid"] = not errors
    verification["errors"] = errors
    verification["bound_expression_count"] = bound_count
    return verification


def source_preserved_expression_allowlist_from_verified_ledger(
    protocol_ir: Mapping[str, Any] | None,
    verification: Mapping[str, Any] | None,
    *,
    validation_entry: str | Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(protocol_ir, Mapping) or not isinstance(verification, Mapping):
        return []
    if not verification.get("valid"):
        return []
    trusted = verification.get("records")
    if not isinstance(trusted, Mapping):
        return []
    occurrences = _source_preserved_ir_occurrences(protocol_ir)
    provenance_counts = Counter(str(item["expression"].get("provenance_id") or "") for item in occurrences)
    entry = str(validation_entry) if validation_entry is not None else ""
    allowances: list[dict[str, Any]] = []
    for occurrence in occurrences:
        expression = occurrence["expression"]
        provenance_id = str(expression.get("provenance_id") or "")
        if not provenance_id or provenance_counts[provenance_id] != 1:
            continue
        source_record = trusted.get(provenance_id)
        if not isinstance(source_record, Mapping):
            continue
        if not _expression_matches_verified_record(occurrence, source_record):
            continue
        source_entry = str(source_record.get("source_entry") or "")
        allowances.append(
            {
                "entry": entry,
                "source_entry": source_entry,
                "accepted_source_entry": source_entry,
                "command": occurrence["command"],
                "field": occurrence["field"],
                "source": str(expression.get("source") or ""),
                "source_hash": str(expression.get("source_hash") or ""),
                "provenance_id": provenance_id,
                "provenance_policy": "verified_source_ledger",
                "source_preservation_allowed": True,
                "source_line_number": source_record.get("line_number"),
                "source_command_index": source_record.get("command_index"),
            }
        )
    return allowances


def _scan_source_artifact(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = path.read_bytes()
    artifact_sha256 = _sha256_bytes(payload)
    if zipfile.is_zipfile(path):
        records: list[dict[str, Any]] = []
        entry_count = 0
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                entry = info.filename
                if not entry.replace("\\", "/").casefold().endswith(".xscr"):
                    continue
                entry_count += 1
                xscr_bytes = archive.read(info)
                records.extend(
                    _records_from_xscr_bytes(
                        xscr_bytes,
                        source_archive_sha256=artifact_sha256,
                        source_entry=f"{path.name}!{entry}",
                        archive_entry=entry,
                    )
                )
        return (
            {
                "kind": "zeia",
                "name": path.name,
                "sha256": artifact_sha256,
                "xscr_entry_count": entry_count,
            },
            records,
        )
    if path.suffix.casefold() == ".xscr":
        records = _records_from_xscr_bytes(
            payload,
            source_archive_sha256=artifact_sha256,
            source_entry=str(path.resolve()),
            archive_entry=path.name,
        )
        return (
            {
                "kind": "xscr",
                "name": path.name,
                "sha256": artifact_sha256,
                "xscr_entry_count": 1,
            },
            records,
        )
    return (
        {
            "kind": "unsupported",
            "name": path.name,
            "sha256": artifact_sha256,
            "xscr_entry_count": 0,
        },
        [],
    )


def _records_from_xscr_bytes(
    payload: bytes,
    *,
    source_archive_sha256: str,
    source_entry: str,
    archive_entry: str,
) -> list[dict[str, Any]]:
    text = _decode_xml_bytes(payload)
    inventory = expression_inventory_from_xscr_text(text, entry=archive_entry)
    xscr_sha256 = _sha256_bytes(payload)
    records: list[dict[str, Any]] = []
    for item in inventory.get("records") or []:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("raw_expression") or "")
        if not source.strip() or try_parse_expression(source) is not None:
            continue
        referenced_variables, referenced_functions = extract_opaque_expression_references(
            source
        )
        record = {
            "source_archive_sha256": source_archive_sha256,
            "source_entry": source_entry,
            "archive_entry": archive_entry,
            "source_xscr_sha256": xscr_sha256,
            "object_name": str(item.get("script") or ""),
            "command": str(item.get("command") or ""),
            "field": str(item.get("field") or ""),
            "line_number": item.get("line"),
            "command_index": item.get("command_index"),
            "expression_sha256": _expression_sha256(source),
            "reference_metadata_origin": "source_ingestion",
            "referenced_variables": list(referenced_variables),
            "referenced_functions": list(referenced_functions),
        }
        record["record_id"] = _record_id(record)
        records.append(record)
    return records


def _bind_protocol(protocol: dict[str, Any], records: list[Mapping[str, Any]]) -> None:
    for occurrence in _source_preserved_ir_occurrences(protocol):
        expression = occurrence["expression"]
        expression.pop("provenance_id", None)
        expression.pop("reference_metadata_origin", None)
        expression.pop("referenced_variables", None)
        expression.pop("referenced_functions", None)
        candidates = [
            record
            for record in records
            if _expression_occurrence_matches_record(occurrence, record)
        ]
        if len(candidates) == 1:
            record = candidates[0]
            expression["provenance_id"] = str(record["record_id"])
            expression["reference_metadata_origin"] = "source_ingestion"
            expression["referenced_variables"] = list(
                record.get("referenced_variables") or []
            )
            expression["referenced_functions"] = list(
                record.get("referenced_functions") or []
            )


def _source_preserved_ir_occurrences(protocol_ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    protocols = protocol_ir.get("protocols") if isinstance(protocol_ir.get("protocols"), list) else [protocol_ir]
    for protocol in protocols:
        if not isinstance(protocol, Mapping):
            continue
        for step in protocol.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            command = _step_expression_command(step)
            if not command:
                continue
            line_number = _step_identity_value(step, "line_number")
            command_index = _step_identity_value(step, "command_index")
            _collect_ir_occurrences(
                step,
                command,
                occurrences,
                line_number=line_number,
                command_index=command_index,
            )
            params = step.get("parameters")
            if isinstance(params, Mapping):
                _collect_ir_occurrences(
                    params,
                    command,
                    occurrences,
                    line_number=line_number,
                    command_index=command_index,
                )
                for section in ("variable_mappings_start", "variable_mappings_end"):
                    for mapping in params.get(section) or []:
                        if isinstance(mapping, Mapping):
                            _collect_ir_occurrences(
                                mapping,
                                "VariableMapping",
                                occurrences,
                                line_number=line_number,
                                command_index=command_index,
                            )
                for variable in params.get("variables") or []:
                    if isinstance(variable, Mapping):
                        _collect_ir_occurrences(
                            variable,
                            "SetVariableStatement",
                            occurrences,
                            line_number=line_number,
                            command_index=command_index,
                        )
    return occurrences


def _collect_ir_occurrences(
    container: Mapping[str, Any],
    command: str,
    occurrences: list[dict[str, Any]],
    *,
    line_number: Any,
    command_index: Any,
) -> None:
    for key, value in container.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict) or item.get("kind") != "source_preserved_expression":
                continue
            field = _field_path_for_expression_key(str(key))
            if not field:
                continue
            occurrences.append(
                {
                    "expression": item,
                    "command": command,
                    "field": field,
                    "line_number": line_number,
                    "command_index": command_index,
                }
            )


def _expression_occurrence_matches_record(
    occurrence: Mapping[str, Any],
    record: Mapping[str, Any],
) -> bool:
    expression = occurrence["expression"]
    if str(record.get("command") or "") != str(occurrence.get("command") or ""):
        return False
    if str(record.get("field") or "") != str(occurrence.get("field") or ""):
        return False
    if str(record.get("expression_sha256") or "") != _expression_sha256(str(expression.get("source") or "")):
        return False
    if not _source_entries_match(
        str(expression.get("source_entry") or ""),
        str(record.get("source_entry") or ""),
        str(record.get("archive_entry") or ""),
    ):
        return False
    line_number = occurrence.get("line_number")
    source_line_number = record.get("line_number")
    if source_line_number not in (None, "") and line_number in (None, ""):
        return False
    if line_number not in (None, "") and str(line_number) != str(source_line_number or ""):
        return False
    command_index = occurrence.get("command_index")
    source_command_index = record.get("command_index")
    if source_command_index not in (None, "") and command_index in (None, ""):
        return False
    if command_index not in (None, "") and str(command_index) != str(source_command_index or ""):
        return False
    return True


def _expression_matches_verified_record(
    occurrence: Mapping[str, Any],
    record: Mapping[str, Any],
) -> bool:
    expression = occurrence["expression"]
    source = str(expression.get("source") or "")
    if str(expression.get("source_hash") or "") != _expression_sha256(source):
        return False
    if expression.get("reference_metadata_origin") != "source_ingestion":
        return False
    referenced_variables = expression.get("referenced_variables")
    referenced_functions = expression.get("referenced_functions")
    if not isinstance(referenced_variables, list) or not isinstance(
        referenced_functions, list
    ):
        return False
    if referenced_variables != list(record.get("referenced_variables") or []):
        return False
    if referenced_functions != list(
        record.get("referenced_functions") or []
    ):
        return False
    return _expression_occurrence_matches_record(occurrence, record)


def _load_bundle_protocol_ir(bundle_root: Path) -> dict[str, Any] | None:
    for candidate in (
        bundle_root / "source" / "protocol.ir.json",
        bundle_root / "protocol.ir.json",
    ):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _load_bundle_expression_provenance(bundle_root: Path) -> dict[str, Any] | None:
    for candidate in (
        bundle_root / "source" / "reports" / EXPRESSION_PROVENANCE_FILENAME,
        bundle_root / "reports" / EXPRESSION_PROVENANCE_FILENAME,
        bundle_root / EXPRESSION_PROVENANCE_FILENAME,
    ):
        ledger = load_expression_provenance_ledger(candidate)
        if ledger is not None:
            return ledger
    return None


def _bundle_source_artifacts(bundle_root: Path) -> list[Path]:
    candidates = [
        bundle_root / "source" / "original-sources",
        bundle_root / "original-sources",
    ]
    for root in candidates:
        if root.is_dir():
            return sorted(path for path in root.rglob("*") if path.is_file())
    return []


def _field_path_for_expression_key(expression_key: str) -> str:
    for field_path in registered_expression_field_paths():
        if canonical_expression_key(field_path) == expression_key:
            return field_path
    return ""


def _step_expression_command(step: Mapping[str, Any]) -> str:
    params = step.get("parameters")
    raw = step.get("command_id")
    if not raw and isinstance(params, Mapping):
        raw = params.get("command_id")
    if raw:
        return canonical_expression_command_id(str(raw))
    return {
        "add_labware": "AddLabwareDataV1",
        "conditional_branch": "ConditionalGroup",
        "default_branch": "ConditionalGroup",
        "loop_over_wells": "LoopGroup",
        "set_variable": "SetVariableStatement",
        "set_remaining_runtime": "SetVariableStatement",
        "move_axis_command": "MoveAxisCommand",
        "runtime_variable_prompt": "SetVariableStatement",
    }.get(str(step.get("operation") or ""), "")


def _step_identity_value(step: Mapping[str, Any], key: str) -> Any:
    params = step.get("parameters")
    if isinstance(params, Mapping) and params.get(key) not in (None, ""):
        return params.get(key)
    return step.get(key)


def _source_entries_match(ir_entry: str, ledger_entry: str, archive_entry: str) -> bool:
    if ir_entry == ledger_entry:
        return True
    normalized_ir = _archive_entry_from_source_entry(ir_entry).replace("\\", "/").casefold()
    normalized_ledger = _archive_entry_from_source_entry(ledger_entry).replace("\\", "/").casefold()
    normalized_archive = archive_entry.replace("\\", "/").casefold()
    if normalized_ir and normalized_ir in {normalized_ledger, normalized_archive}:
        return True
    if "!" in ir_entry:
        return False
    return Path(ir_entry).name.casefold() == Path(ledger_entry).name.casefold()


def _archive_entry_from_source_entry(source_entry: str) -> str:
    text = str(source_entry or "")
    if "!" in text:
        return text.rsplit("!", 1)[-1]
    return text


def _record_id(record: Mapping[str, Any]) -> str:
    return "exprprov:" + hashlib.sha256(
        json.dumps(_record_identity(record), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _record_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    identity = {
        key: record.get(key)
        for key in (
            "source_archive_sha256",
            "source_xscr_sha256",
            "object_name",
            "command",
            "field",
            "line_number",
            "command_index",
            "expression_sha256",
            "reference_metadata_origin",
            "referenced_variables",
            "referenced_functions",
        )
    }
    identity["archive_entry"] = (
        record.get("archive_entry")
        if "!" in str(record.get("source_entry") or "")
        else ""
    )
    return identity


def _ledger_sha256(ledger: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": ledger.get("schema_version"),
        "source_artifacts": [
            {
                "kind": item.get("kind"),
                "name": item.get("name"),
                "sha256": item.get("sha256"),
                "xscr_entry_count": item.get("xscr_entry_count"),
            }
            for item in ledger.get("source_artifacts") or []
            if isinstance(item, Mapping)
        ],
        "records": [
            dict(item)
            for item in ledger.get("records") or []
            if isinstance(item, Mapping)
        ],
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _source_artifact_identity_counts(ledger: Mapping[str, Any]) -> Counter[tuple[str, str, int]]:
    return Counter(
        (
            str(item.get("kind") or ""),
            str(item.get("sha256") or ""),
            int(item.get("xscr_entry_count") or 0),
        )
        for item in ledger.get("source_artifacts") or []
        if isinstance(item, Mapping)
    )


def _expression_sha256(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _decode_xml_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
