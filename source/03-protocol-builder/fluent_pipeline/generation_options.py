"""Typed generation-option model shared across CLI, MCP, specs, and workflow."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
import re
from typing import Any, Mapping


DEFAULT_MAX_REPAIR_ITERATIONS = 1
DEFAULT_FLUENT_PROVIDER = "auto"
DEFAULT_FLUENT_TIMEOUT = 180.0
DEFAULT_VERIFICATION_PROMPT_RUP = "mixed"
VERIFICATION_PROMPT_RUP_MODES = frozenset({"mixed", "standard", "worktable"})
WORKTABLE_ONLY_IMAGE_MAX_VERSION = (3, 6)


@dataclass(frozen=True)
class GenerationOptions:
    simulate: bool = True
    compile_xscr: bool = True
    max_repair_iterations: int = DEFAULT_MAX_REPAIR_ITERATIONS
    strict_readiness: bool = False
    apply_modeling: bool = False
    verification_prompt_rup: str = DEFAULT_VERIFICATION_PROMPT_RUP
    target_fluentcontrol_version: str | None = None
    target_script_folder: str | None = None
    approve_partial_zeia: bool = False
    approve_deck_layout: bool = False
    approve_command_inventory: bool = False
    approve_unsupported_raw_xml: bool = False
    approved_unsupported_command_ids: tuple[str, ...] = ()
    waive_checksum_recompute: bool = False
    preserve_failed_artifacts: bool = False
    preserve_regeneration_baseline: bool = False
    fluent_context_check: bool = False
    fluent_provider: str = DEFAULT_FLUENT_PROVIDER
    fluent_timeout: float = DEFAULT_FLUENT_TIMEOUT
    project_archive: Path | None = field(default=None, metadata={"serialize": False})
    project_name: str | None = field(default=None, metadata={"serialize": False})
    force_import: bool = field(default=False, metadata={"serialize": False})
    pattern_refs: tuple[str, ...] = field(default_factory=tuple, metadata={"serialize": False})
    index_db: Path | None = field(default=None, metadata={"serialize": False})
    pattern_ids: tuple[int | str, ...] = field(default_factory=tuple, metadata={"serialize": False})
    pattern_queries: tuple[str, ...] = field(default_factory=tuple, metadata={"serialize": False})
    source_script_rank: int = field(default=1, metadata={"serialize": False})
    protocol_name: str | None = field(default=None, metadata={"serialize": False})
    subroutine_dirs: tuple[Path, ...] = field(default_factory=tuple, metadata={"serialize": False})
    record_snapshots: bool | str | None = field(default=None, metadata={"serialize": False})
    deterministic_compile: bool = field(default=False, metadata={"serialize": False})
    fluent_method: str | None = field(default=None, metadata={"serialize": False})
    fluent_command: str | None = field(default=None, metadata={"serialize": False})
    fluent_host: str = field(default="127.0.0.1", metadata={"serialize": False})
    fluent_port: int = field(default=50052, metadata={"serialize": False})
    fluent_insecure: bool = field(default=False, metadata={"serialize": False})

    def as_dict(self) -> dict[str, Any]:
        """Return durable request-spec generation options as a mapping."""
        out: dict[str, Any] = {}
        for item in fields(self):
            if not item.metadata.get("serialize", True):
                continue
            value = getattr(self, item.name)
            out[item.name] = list(value) if isinstance(value, tuple) else value
        return out

    def runtime_dict(self) -> dict[str, Any]:
        """Return adapter-only runtime options excluded from request specs."""
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if not item.metadata.get("serialize", True)
        }


def normalize_generation_options(
    options: GenerationOptions | Mapping[str, Any] | None = None,
    *,
    simulate: bool | None = None,
    compile_xscr: bool | None = None,
    max_repair_iterations: int | None = None,
    strict_readiness: bool | None = None,
    apply_modeling: bool | None = None,
    verification_prompt_rup: str | None = None,
    target_fluentcontrol_version: str | None = None,
    target_script_folder: str | None = None,
    approve_partial_zeia: bool | None = None,
    approve_deck_layout: bool | None = None,
    approve_command_inventory: bool | None = None,
    approve_unsupported_raw_xml: bool | None = None,
    approved_unsupported_command_ids: tuple[str, ...] | list[str] | None = None,
    waive_checksum_recompute: bool | None = None,
    preserve_failed_artifacts: bool | None = None,
    preserve_regeneration_baseline: bool | None = None,
    fluent_context_check: bool | None = None,
    fluent_provider: str | None = None,
    fluent_timeout: float | None = None,
) -> GenerationOptions:
    base = _coerce_generation_options(options)
    target_version = _optional_text(
        base.target_fluentcontrol_version
        if target_fluentcontrol_version is None
        else target_fluentcontrol_version
    )
    script_folder = _optional_text(
        base.target_script_folder if target_script_folder is None else target_script_folder
    )
    requested_rup = _normalize_verification_prompt_rup(
        base.verification_prompt_rup if verification_prompt_rup is None else verification_prompt_rup
    )
    effective_rup = "worktable" if fluent_version_requires_worktable_images(target_version) else requested_rup
    return GenerationOptions(
        simulate=base.simulate if simulate is None else bool(simulate),
        compile_xscr=base.compile_xscr if compile_xscr is None else bool(compile_xscr),
        max_repair_iterations=max(
            0,
            int(base.max_repair_iterations if max_repair_iterations is None else max_repair_iterations),
        ),
        strict_readiness=base.strict_readiness if strict_readiness is None else bool(strict_readiness),
        apply_modeling=base.apply_modeling if apply_modeling is None else bool(apply_modeling),
        verification_prompt_rup=effective_rup,
        target_fluentcontrol_version=target_version,
        target_script_folder=script_folder,
        approve_partial_zeia=base.approve_partial_zeia if approve_partial_zeia is None else bool(approve_partial_zeia),
        approve_deck_layout=base.approve_deck_layout if approve_deck_layout is None else bool(approve_deck_layout),
        approve_command_inventory=(
            base.approve_command_inventory
            if approve_command_inventory is None
            else bool(approve_command_inventory)
        ),
        approve_unsupported_raw_xml=(
            base.approve_unsupported_raw_xml
            if approve_unsupported_raw_xml is None
            else bool(approve_unsupported_raw_xml)
        ),
        approved_unsupported_command_ids=(
            base.approved_unsupported_command_ids
            if approved_unsupported_command_ids is None
            else tuple(str(value) for value in approved_unsupported_command_ids if str(value))
        ),
        waive_checksum_recompute=(
            base.waive_checksum_recompute
            if waive_checksum_recompute is None
            else bool(waive_checksum_recompute)
        ),
        preserve_failed_artifacts=(
            base.preserve_failed_artifacts
            if preserve_failed_artifacts is None
            else bool(preserve_failed_artifacts)
        ),
        preserve_regeneration_baseline=(
            base.preserve_regeneration_baseline
            if preserve_regeneration_baseline is None
            else bool(preserve_regeneration_baseline)
        ),
        fluent_context_check=(
            base.fluent_context_check
            if fluent_context_check is None
            else bool(fluent_context_check)
        ),
        fluent_provider=str(base.fluent_provider if fluent_provider is None else fluent_provider),
        fluent_timeout=float(base.fluent_timeout if fluent_timeout is None else fluent_timeout),
        project_archive=base.project_archive,
        project_name=base.project_name,
        force_import=base.force_import,
        pattern_refs=base.pattern_refs,
        index_db=base.index_db,
        pattern_ids=base.pattern_ids,
        pattern_queries=base.pattern_queries,
        source_script_rank=base.source_script_rank,
        protocol_name=base.protocol_name,
        subroutine_dirs=base.subroutine_dirs,
        record_snapshots=base.record_snapshots,
        deterministic_compile=base.deterministic_compile,
        fluent_method=base.fluent_method,
        fluent_command=base.fluent_command,
        fluent_host=base.fluent_host,
        fluent_port=base.fluent_port,
        fluent_insecure=base.fluent_insecure,
    )


def generation_options_from_request_spec(spec: Mapping[str, Any]) -> GenerationOptions:
    generation = spec.get("generation") if isinstance(spec, Mapping) else None
    source = spec.get("source") if isinstance(spec, Mapping) else None
    generation_map = generation if isinstance(generation, Mapping) else {}
    source_map = source if isinstance(source, Mapping) else {}
    full_zeia = source_map.get("full_zeia_export")
    full_zeia_map = full_zeia if isinstance(full_zeia, Mapping) else {}
    approve_partial = bool(
        generation_map.get("approve_partial_zeia", False)
        or full_zeia_map.get("approved_partial_zeia", False)
    )
    return normalize_generation_options(generation_map, approve_partial_zeia=approve_partial)


def generation_options_from_cli_args(
    args: Any,
    *,
    defaults: GenerationOptions | Mapping[str, Any] | None = None,
) -> GenerationOptions:
    base = _coerce_generation_options(defaults)
    no_simulate = getattr(args, "no_simulate", None)
    no_compile = getattr(args, "no_compile", None)
    return normalize_generation_options(
        base,
        simulate=base.simulate if not no_simulate else False,
        compile_xscr=base.compile_xscr if not no_compile else False,
        max_repair_iterations=_optional_attr(args, "max_repair_iterations"),
        strict_readiness=base.strict_readiness or bool(getattr(args, "strict_readiness", False)),
        apply_modeling=base.apply_modeling or bool(getattr(args, "apply_modeling", False)),
        target_fluentcontrol_version=_optional_attr(args, "target_fluentcontrol_version"),
        target_script_folder=_optional_attr(args, "target_script_folder"),
        approve_partial_zeia=base.approve_partial_zeia or bool(getattr(args, "approve_partial_zeia", False)),
        approve_deck_layout=base.approve_deck_layout or bool(getattr(args, "approve_deck_layout", False)),
        approve_command_inventory=(
            base.approve_command_inventory or bool(getattr(args, "approve_command_inventory", False))
        ),
        approve_unsupported_raw_xml=(
            base.approve_unsupported_raw_xml or bool(getattr(args, "approve_unsupported_raw_xml", False))
        ),
        approved_unsupported_command_ids=tuple(
            dict.fromkeys(
                [
                    *base.approved_unsupported_command_ids,
                    *(str(value) for value in (getattr(args, "approved_unsupported_command_ids", None) or [])),
                ]
            )
        ),
        waive_checksum_recompute=(
            base.waive_checksum_recompute or bool(getattr(args, "waive_checksum_recompute", False))
        ),
        preserve_failed_artifacts=(
            base.preserve_failed_artifacts or bool(getattr(args, "preserve_failed_artifacts", False))
        ),
        preserve_regeneration_baseline=(
            base.preserve_regeneration_baseline
            or bool(getattr(args, "preserve_regeneration_baseline", False))
        ),
        fluent_context_check=base.fluent_context_check or bool(getattr(args, "fluent_context_check", False)),
        fluent_provider=_optional_attr(args, "fluent_provider"),
        fluent_timeout=_optional_attr(args, "fluent_timeout"),
    )


def _optional_attr(args: Any, name: str) -> Any:
    return getattr(args, name, None)


def _coerce_generation_options(
    options: GenerationOptions | Mapping[str, Any] | None,
) -> GenerationOptions:
    if isinstance(options, GenerationOptions):
        return options
    if not isinstance(options, Mapping):
        return GenerationOptions()
    return GenerationOptions(
        simulate=bool(options.get("simulate", True)),
        compile_xscr=bool(options.get("compile_xscr", True)),
        max_repair_iterations=max(
            0,
            int(options.get("max_repair_iterations", DEFAULT_MAX_REPAIR_ITERATIONS) or 0),
        ),
        strict_readiness=bool(options.get("strict_readiness", False)),
        apply_modeling=bool(options.get("apply_modeling", False)),
        verification_prompt_rup=_normalize_verification_prompt_rup(
            options.get("verification_prompt_rup", DEFAULT_VERIFICATION_PROMPT_RUP)
        ),
        target_fluentcontrol_version=_optional_text(options.get("target_fluentcontrol_version")),
        target_script_folder=_optional_text(options.get("target_script_folder")),
        approve_partial_zeia=bool(options.get("approve_partial_zeia", False)),
        approve_deck_layout=bool(options.get("approve_deck_layout", False)),
        approve_command_inventory=bool(options.get("approve_command_inventory", False)),
        approve_unsupported_raw_xml=bool(options.get("approve_unsupported_raw_xml", False)),
        approved_unsupported_command_ids=tuple(
            str(value) for value in (options.get("approved_unsupported_command_ids") or []) if str(value)
        ),
        waive_checksum_recompute=bool(options.get("waive_checksum_recompute", False)),
        preserve_failed_artifacts=bool(options.get("preserve_failed_artifacts", False)),
        preserve_regeneration_baseline=bool(
            options.get("preserve_regeneration_baseline", False)
        ),
        fluent_context_check=bool(options.get("fluent_context_check", False)),
        fluent_provider=str(options.get("fluent_provider", DEFAULT_FLUENT_PROVIDER) or DEFAULT_FLUENT_PROVIDER),
        fluent_timeout=float(options.get("fluent_timeout", DEFAULT_FLUENT_TIMEOUT) or DEFAULT_FLUENT_TIMEOUT),
        project_archive=_optional_path(options.get("project_archive")),
        project_name=_optional_text(options.get("project_name")),
        force_import=bool(options.get("force_import", False)),
        pattern_refs=tuple(str(value) for value in (options.get("pattern_refs") or [])),
        index_db=_optional_path(options.get("index_db")),
        pattern_ids=tuple(options.get("pattern_ids") or []),
        pattern_queries=tuple(str(value) for value in (options.get("pattern_queries") or [])),
        source_script_rank=max(1, int(options.get("source_script_rank", 1) or 1)),
        protocol_name=_optional_text(options.get("protocol_name")),
        subroutine_dirs=tuple(
            path
            for path in (_optional_path(value) for value in (options.get("subroutine_dirs") or []))
            if path is not None
        ),
        record_snapshots=options.get("record_snapshots"),
        deterministic_compile=bool(options.get("deterministic_compile", False)),
        fluent_method=_optional_text(options.get("fluent_method")),
        fluent_command=_optional_text(options.get("fluent_command")),
        fluent_host=str(options.get("fluent_host", "127.0.0.1") or "127.0.0.1"),
        fluent_port=int(options.get("fluent_port", 50052) or 50052),
        fluent_insecure=bool(options.get("fluent_insecure", False)),
    )


def _optional_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value)).expanduser()


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _normalize_verification_prompt_rup(value: Any) -> str:
    mode = str(value or DEFAULT_VERIFICATION_PROMPT_RUP).strip().lower()
    if mode not in VERIFICATION_PROMPT_RUP_MODES:
        return DEFAULT_VERIFICATION_PROMPT_RUP
    return mode


def fluent_version_requires_worktable_images(value: Any) -> bool:
    version = _fluent_major_minor(value)
    return version is not None and version < WORKTABLE_ONLY_IMAGE_MAX_VERSION


def _fluent_major_minor(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))
