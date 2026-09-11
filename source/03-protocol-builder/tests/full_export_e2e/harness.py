"""Helpers for the synthetic full-export end-to-end suite."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import fluent_pipeline.config as pipeline_config
import fluent_pipeline.project_context as project_context
from fluent_pipeline.cli.parser import _build_parser
from fluent_pipeline.cli.runtime import main as cli_main
from fluent_pipeline.generation_workflow import GENERATION_STAGES
from fluent_pipeline.mcp_gateway import ProtocolBuilderGateway
from fluent_pipeline.runner import PipelineError


PROTOCOL_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROTOCOL_ROOT.parent
for _sibling in ("00-shared", "01-project-reader", "02-worklist-builder"):
    _sibling_path = str(SOURCE_ROOT / _sibling)
    if _sibling_path not in sys.path:
        sys.path.insert(0, _sibling_path)
FIXTURE_DIR = PROTOCOL_ROOT / "tests" / "fixtures" / "full_export_e2e"
RECIPE_PATH = FIXTURE_DIR / "complete.zeia.json"
MANIFEST_PATH = PROTOCOL_ROOT / "tests" / "full_export_e2e" / "feature_coverage_manifest.json"
ZEIA_RECIPE_SCHEMA_VERSION = "tecan.synthetic_zeia_recipe.v1"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
CONTEXT_NAME = "full-export-e2e"
PATH_ATTRS = (
    "READY_TO_IMPORT_DIR",
    "PROJECTS_DIR",
    "COLLECTIONS_DIR",
    "SHARED_TEMP_DIR",
    "ACTIVE_CONTEXT_FILE",
    "CACHE_DIR",
    "PACKAGE_STAGING_DIR",
    "FAILED_PACKAGES_DIR",
    "LOGS_DIR",
    "SHARED_BUILD_DIR",
    "CATALOG_CACHE_DIR",
    "ZEIA_REFERENCE_CACHE_DIR",
)


def load_feature_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("feature coverage manifest must be a JSON object")
    return payload


def materialize_complete_zeia(output_dir: Path, recipe_path: Path = RECIPE_PATH) -> Path:
    """Build a temporary ZEIA from the immutable recipe. Never writes back to fixtures."""
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    if recipe.get("schema_version") != ZEIA_RECIPE_SCHEMA_VERSION:
        raise ValueError(f"unsupported ZEIA recipe schema: {recipe.get('schema_version')!r}")
    archive_name = str(recipe.get("archive_name") or "")
    if not archive_name.endswith(".zeia") or Path(archive_name).name != archive_name:
        raise ValueError(f"unsafe ZEIA recipe archive name: {archive_name!r}")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / archive_name
    with zipfile.ZipFile(archive, "w") as zf:
        for entry in sorted(recipe.get("entries") or [], key=lambda item: item["archive_path"]):
            archive_path = PurePosixPath(str(entry["archive_path"]))
            if archive_path.is_absolute() or ".." in archive_path.parts or not archive_path.name:
                raise ValueError(f"unsafe ZEIA recipe archive path: {archive_path}")
            source = _resolve_protocol_path(str(entry["source"]))
            if not source.is_file():
                raise FileNotFoundError(source)
            info = zipfile.ZipInfo(archive_path.as_posix(), date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, source.read_bytes())
    return archive


def copy_archive(archive: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(archive.read_bytes())
    return destination


@contextlib.contextmanager
def isolated_pipeline_home(home: Path) -> Iterator[dict[str, Path]]:
    """Redirect ready-to-import / project-context paths into a temp workspace."""
    ready = (home / "ready-to-import").resolve()
    shared = ready / "_shared" / "temp_files"
    shared.mkdir(parents=True, exist_ok=True)
    replacements = {
        "READY_TO_IMPORT_DIR": ready,
        "PROJECTS_DIR": ready,
        "COLLECTIONS_DIR": ready,
        "SHARED_TEMP_DIR": shared,
        "ACTIVE_CONTEXT_FILE": shared / ".active_context",
        "CACHE_DIR": shared / "cache",
        "PACKAGE_STAGING_DIR": shared / "package-staging",
        "FAILED_PACKAGES_DIR": shared / "failed-packages",
        "LOGS_DIR": shared / "logs",
        "SHARED_BUILD_DIR": shared / "build",
        "CATALOG_CACHE_DIR": shared / "cache" / "catalog",
        "ZEIA_REFERENCE_CACHE_DIR": shared / "cache" / "zeia-references",
    }
    originals: dict[tuple[str, str], Any] = {}
    try:
        for attr, value in replacements.items():
            if hasattr(pipeline_config, attr):
                originals[("fluent_pipeline.config", attr)] = getattr(pipeline_config, attr)
                setattr(pipeline_config, attr, value)
        for name, module in list(sys.modules.items()):
            if not name.startswith("fluent_pipeline") or module is pipeline_config:
                continue
            for attr, value in replacements.items():
                if not hasattr(module, attr):
                    continue
                key = (name, attr)
                if key not in originals:
                    originals[key] = getattr(module, attr)
                setattr(module, attr, value)
        project_context.PROJECTS_DIR = ready
        project_context.COLLECTIONS_DIR = ready
        project_context.ACTIVE_CONTEXT_FILE = shared / ".active_context"
        yield {"home": home.resolve(), "ready": ready, "shared": shared}
    finally:
        for (name, attr), value in originals.items():
            module = sys.modules.get(name)
            if module is not None:
                setattr(module, attr, value)


def run_cli(argv: list[str]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli_main(argv)
    except PipelineError as exc:
        return {
            "argv": argv,
            "code": 2,
            "stdout": stdout.getvalue(),
            "stderr": f"{stderr.getvalue()}Pipeline error: {exc}\n",
            "error": str(exc),
        }
    except SystemExit as exc:
        return {
            "argv": argv,
            "code": int(exc.code or 0),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "error": None,
        }
    except Exception as exc:
        return {
            "argv": argv,
            "code": 2,
            "stdout": stdout.getvalue(),
            "stderr": f"{stderr.getvalue()}{type(exc).__name__}: {exc}\n",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "argv": argv,
        "code": int(code or 0),
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "error": None,
    }


def live_cli_commands() -> list[str]:
    parser = _build_parser()
    return sorted(_subparser_names(parser))


def live_mcp_tools() -> list[str]:
    from fluent_pipeline import mcp_server

    text = Path(mcp_server.__file__).read_text(encoding="utf-8")
    return sorted(set(re.findall(r"^def (fluent_[a-z0-9_]+)\(", text, flags=re.MULTILINE)))


def live_generation_stages() -> list[str]:
    return [stage_id for stage_id, _title in GENERATION_STAGES]


def live_reader_commands() -> list[str]:
    import tecan_reader.cli as reader_cli

    return _reader_or_worklist_ids(reader_cli)


def live_worklist_commands() -> list[str]:
    import tecan_worklist.cli as worklist_cli

    return _reader_or_worklist_ids(worklist_cli)


def discover_required_feature_ids() -> set[str]:
    ids = {f"cli.{name}" for name in live_cli_commands()}
    ids.update(f"mcp.{name}" for name in live_mcp_tools())
    ids.update(f"stage.{name}" for name in live_generation_stages())
    ids.update(f"reader.{name}" for name in live_reader_commands())
    ids.update(f"worklist.{name}" for name in live_worklist_commands())
    manifest = load_feature_manifest()
    for item in manifest.get("pipeline_features") or []:
        ids.add(str(item["id"]))
    return ids


def resolved_features() -> list[dict[str, Any]]:
    manifest = load_feature_manifest()
    defaults: Mapping[str, Any] = manifest.get("default_test_groups") or {}
    overrides: Mapping[str, Any] = manifest.get("feature_overrides") or {}
    features: dict[str, dict[str, Any]] = {}
    for feature_id in sorted(discover_required_feature_ids()):
        group = feature_id.split(".", 1)[0]
        tests = list(defaults.get(group) or defaults.get("workflow") or [])
        record = {
            "id": feature_id,
            "tests": tests,
            "coverage": "full_export",
            "gap_reason": None,
        }
        override = overrides.get(feature_id) or {}
        record.update({key: override[key] for key in override})
        if "tests" in override:
            record["tests"] = list(override["tests"])
        features[feature_id] = record
    for item in manifest.get("pipeline_features") or []:
        feature_id = str(item["id"])
        current = features.get(feature_id, {"id": feature_id, "tests": [], "coverage": "full_export", "gap_reason": None})
        current.update({key: item[key] for key in item if key != "id"})
        if "tests" in item:
            current["tests"] = list(item["tests"])
        features[feature_id] = current
    return [features[key] for key in sorted(features)]


def gateway() -> ProtocolBuilderGateway:
    return ProtocolBuilderGateway()


def _subparser_names(parser: argparse.ArgumentParser) -> list[str]:
    names: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.extend(action.choices)
    return names


def _nested_command_ids(parser: argparse.ArgumentParser, prefix: str = "") -> list[str]:
    ids: list[str] = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            feature_id = name if not prefix else f"{prefix}.{name}"
            child_ids = _nested_command_ids(subparser, feature_id)
            if child_ids:
                ids.extend(child_ids)
            else:
                ids.append(feature_id)
    return ids


def _reader_or_worklist_ids(module: Any) -> list[str]:
    """Build the same CLI parser the package exposes without executing a command."""
    parser = argparse.ArgumentParser(prog=getattr(module, "main").__module__)
    # Importing the module does not expose a builder; reconstruct by executing
    # main() with --help is noisy. Walk by re-calling the source parser via a
    # private reconstruction: invoke main with a captured parser.
    captured: dict[str, argparse.ArgumentParser] = {}

    original_parse = argparse.ArgumentParser.parse_args

    def _capture(self: argparse.ArgumentParser, args=None, namespace=None):  # type: ignore[no-untyped-def]
        captured["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = _capture  # type: ignore[method-assign]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                module.main([])
            except SystemExit:
                pass
    finally:
        argparse.ArgumentParser.parse_args = original_parse  # type: ignore[method-assign]
    parser = captured.get("parser")
    if parser is None:
        raise RuntimeError(f"failed to capture parser for {module.__name__}")
    return sorted(_nested_command_ids(parser))


def _resolve_protocol_path(relative: str) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe protocol-relative path: {relative!r}")
    resolved = (PROTOCOL_ROOT / candidate).resolve()
    try:
        resolved.relative_to(PROTOCOL_ROOT)
    except ValueError as exc:
        raise ValueError(f"path escapes protocol root: {relative!r}") from exc
    return resolved
