"""Shared request construction helpers for CLI and MCP adapters."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from .application_services import RequestSpecCreateRequest
from .generation_options import (
    GenerationOptions,
    generation_options_from_request_spec,
    normalize_generation_options,
)
from .generation_workflow import ApprovalSet, GenerationRequest
from .request_spec import request_verbatim_prompt


def build_approval_set_from_options(options: GenerationOptions) -> ApprovalSet:
    """Build generation approvals from normalized generation options."""
    return ApprovalSet(
        approve_partial_zeia=options.approve_partial_zeia,
        waive_checksum_recompute=options.waive_checksum_recompute,
        approve_deck_layout=options.approve_deck_layout,
        approve_command_inventory=options.approve_command_inventory,
        approve_unsupported_raw_xml=options.approve_unsupported_raw_xml,
    )


def merge_generation_options_from_spec(
    spec: dict[str, Any],
    overrides: GenerationOptions | dict[str, Any] | None = None,
    *,
    mode: str | None = None,
) -> GenerationOptions:
    """Return request-spec generation options with adapter overrides applied."""
    options = generation_options_from_request_spec(spec)
    if overrides is not None:
        override_options = normalize_generation_options(overrides)
        options = normalize_generation_options(options, **override_options.as_dict())
        options = dataclass_replace(options, **override_options.runtime_dict())
    if mode == "final":
        return normalize_generation_options(options, simulate=True, compile_xscr=True)
    if mode == "scaffold":
        return normalize_generation_options(options, simulate=False, compile_xscr=False)
    return options


def build_generation_request(
    *,
    intent: str,
    output_directory: Path,
    context_name: str | None = None,
    source_scripts: tuple[str, ...] | list[str] | None = None,
    protocol_ir: Path | None = None,
    options: GenerationOptions | dict[str, Any] | None = None,
    request_spec_path: Path | None = None,
    option_updates: dict[str, Any] | None = None,
    use_active_context: bool = True,
) -> GenerationRequest:
    """Build a GenerationRequest from already-parsed adapter inputs."""
    normalized_options = normalize_generation_options(options)
    if option_updates:
        normalized_options = dataclass_replace(normalized_options, **option_updates)
    return GenerationRequest(
        intent=intent,
        output_directory=output_directory,
        context_name=context_name,
        source_scripts=tuple(str(value) for value in (source_scripts or ())),
        protocol_ir=protocol_ir,
        options=normalized_options,
        approvals=build_approval_set_from_options(normalized_options),
        request_spec_path=request_spec_path,
        use_active_context=use_active_context,
    )


def build_generation_request_from_spec(
    spec_source: Path,
    *,
    spec: dict[str, Any],
    context: str | None,
    selected_ir: Path | None,
    output_directory: Path,
    mode: str,
    generation_options: GenerationOptions | dict[str, Any] | None = None,
    use_active_context: bool = True,
) -> GenerationRequest:
    """Build a GenerationRequest from a request.spec.yaml payload."""
    options = merge_generation_options_from_spec(spec, generation_options, mode=mode)
    request_data = spec.get("request") if isinstance(spec.get("request"), dict) else {}
    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    pattern_index = source.get("pattern_index") if isinstance(source.get("pattern_index"), dict) else {}
    fluent_method = (
        str(spec.get("generation", {}).get("fluent_method") or "")
        if isinstance(spec.get("generation"), dict)
        else None
    ) or None
    return build_generation_request(
        intent=request_verbatim_prompt(spec),
        output_directory=output_directory,
        context_name=str(context or source.get("context") or "") or None,
        source_scripts=tuple(str(value) for value in (source.get("source_scripts") or [])),
        protocol_ir=selected_ir,
        options=options,
        request_spec_path=spec_source,
        use_active_context=use_active_context,
        option_updates={
            "pattern_refs": tuple(str(value) for value in (source.get("pattern_refs") or [])),
            "index_db": (
                Path(str(pattern_index.get("database"))).expanduser()
                if pattern_index.get("database")
                else None
            ),
            "pattern_ids": tuple(pattern_index.get("pattern_ids") or []),
            "pattern_queries": tuple(str(value) for value in (pattern_index.get("pattern_queries") or [])),
            "source_script_rank": max(1, int(pattern_index.get("source_script_rank", 1) or 1)),
            "protocol_name": str(request_data.get("protocol_name") or "") or None,
            "fluent_method": fluent_method,
        },
    )


def build_request_spec_create_request(
    *,
    intent: str,
    output_path: Path,
    protocol_name: str | None = None,
    context: str | None = None,
    context_kind: str | None = None,
    contexts: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
    project_archives: tuple[Path, ...] | list[Path] | None = None,
    collection: str | None = None,
    source_scripts: tuple[str, ...] | list[str] | None = None,
    pattern_refs: tuple[str, ...] | list[str] | None = None,
    index_db: Path | None = None,
    pattern_ids: tuple[int | str, ...] | list[int | str] | None = None,
    pattern_queries: tuple[str, ...] | list[str] | None = None,
    source_script_rank: int = 1,
    generation_options: GenerationOptions | dict[str, Any] | None = None,
    fluent_method: str | None = None,
) -> RequestSpecCreateRequest:
    """Build a RequestSpecCreateRequest from adapter-normalized fields."""
    return RequestSpecCreateRequest(
        intent=intent,
        output_path=output_path,
        protocol_name=protocol_name,
        context=context,
        context_kind=context_kind,
        contexts=tuple(contexts or ()),
        project_archives=tuple(project_archives or ()),
        collection=collection,
        source_scripts=tuple(source_scripts or ()),
        pattern_refs=tuple(pattern_refs or ()),
        index_db=index_db,
        pattern_ids=tuple(pattern_ids or ()),
        pattern_queries=tuple(pattern_queries or ()),
        source_script_rank=max(1, int(source_script_rank or 1)),
        generation_options=normalize_generation_options(generation_options),
        fluent_method=fluent_method,
    )
