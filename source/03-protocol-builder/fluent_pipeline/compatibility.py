"""FluentControl connector version compatibility reporting."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import tomllib
from . import xml_compat as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import TECAN_AI_DIR


SOURCE_MANUAL = "https://www.tecan.com/knowledge-portal/fluentcontrol-software-manual"
SOURCE_WINDOWS_COMPATIBILITY = (
    "https://www.tecan.com/knowledge-portal/"
    "fluentcontrol-software-compatibility-with-windows-operation-system"
)
SOURCE_UNITELABS_REGISTRY = "https://sila-standard.com/sila_device/unitelabs-tecan-fluentcontrol-connector/"
SOURCE_UNITELABS_GITLAB = "https://gitlab.com/unitelabs/connectors/tecan-fluentcontrol"
SOURCE_TECAN_SAMPLE = "https://gitlab.com/tecan/fluent-sila2-connector.git"

FALLBACK_MANUAL_FLUENT_VERSION = "3.8 SP1"
FALLBACK_MANUAL_VERSION = "399935_en_V2_9"
FALLBACK_WINDOWS_ENVIRONMENT = (
    "Windows 11 (IoT) Enterprise LTSC 2024 or Windows 10 (IoT) Enterprise LTSC 2021"
)
FALLBACK_UNITELABS_TESTED_BUILDS = {
    "3.4.9.61784",
    "3.4.10.62215",
}
FALLBACK_UNITELABS_PACKAGE = {
    "name": "unitelabs-tecan-fluentcontrol",
    "version": "0.3.0",
    "requires_python": ">=3.10,<4.0",
    "dependencies": ("comtypes==1.4.6",),
    "urls": {
        "repository": SOURCE_UNITELABS_GITLAB,
        "documentation": SOURCE_UNITELABS_REGISTRY,
    },
}
FALLBACK_UNITELABS_FEATURE_VERSIONS = ("SiLA 2 1.1 feature XML v1.0",)
LEGACY_WINDOWS_ENVIRONMENT = "Windows 10 Enterprise LTSC 2019 or Windows 10 Enterprise LTSC 2021"


@dataclass(frozen=True)
class Evidence:
    """One sourced claim used by the compatibility matrix."""

    claim: str
    source: str
    confidence: str
    url: str | None = None
    path: str | None = None
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "claim": self.claim,
            "source": self.source,
            "confidence": self.confidence,
        }
        if self.url:
            data["url"] = self.url
        if self.path:
            data["path"] = self.path
        if self.line is not None:
            data["line"] = self.line
        return data


@dataclass(frozen=True)
class ManualMetadata:
    """Local metadata extracted from the cached FluentControl manual."""

    selected_resource: str
    fluentcontrol_version: str
    document_version: str
    windows_environment: str
    source_url: str
    retrieved_on: str
    pdf_path: str
    text_path: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_resource": self.selected_resource,
            "fluentcontrol_version": self.fluentcontrol_version,
            "document_version": self.document_version,
            "windows_environment": self.windows_environment,
            "source_url": self.source_url,
            "retrieved_on": self.retrieved_on,
            "pdf_path": self.pdf_path,
            "text_path": self.text_path,
            "evidence": [item.as_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class ConnectorMetadata:
    """Local metadata for a connector reference."""

    key: str
    name: str
    package_name: str
    package_version: str
    version: str
    commit: str
    commit_date: str
    python_requirement: str
    dependencies: tuple[str, ...]
    package_urls: dict[str, str]
    feature_versions: tuple[str, ...]
    api_references: tuple[str, ...]
    sila_api_compatibility: str
    default_windows_environment: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "package_name": self.package_name,
            "package_version": self.package_version,
            "version": self.version,
            "commit": self.commit,
            "commit_date": self.commit_date,
            "python_requirement": self.python_requirement,
            "dependencies": list(self.dependencies),
            "package_urls": dict(self.package_urls),
            "feature_versions": list(self.feature_versions),
            "api_references": list(self.api_references),
            "sila_api_compatibility": self.sila_api_compatibility,
            "default_windows_environment": self.default_windows_environment,
            "evidence": [item.as_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class CompatibilityRow:
    """One FluentControl/manual/connector compatibility row."""

    connector_key: str
    connector_name: str
    fluentcontrol_version: str
    fluentcontrol_build: str
    manual_version: str
    connector_version: str
    connector_commit: str
    sila_api_compatibility: str
    windows_environment: str
    connector_tested: str
    status: str
    confidence: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "connector_key": self.connector_key,
            "connector_name": self.connector_name,
            "fluentcontrol_version": self.fluentcontrol_version,
            "fluentcontrol_build": self.fluentcontrol_build,
            "manual_version": self.manual_version,
            "connector_version": self.connector_version,
            "connector_commit": self.connector_commit,
            "sila_api_compatibility": self.sila_api_compatibility,
            "windows_environment": self.windows_environment,
            "connector_tested": self.connector_tested,
            "status": self.status,
            "confidence": self.confidence,
            "evidence": [item.as_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class TargetSetup:
    """A setup to classify against known connector compatibility evidence."""

    fluentcontrol_version: str
    fluentcontrol_build: str = "unknown"
    manual_version: str = FALLBACK_MANUAL_VERSION
    windows_environment: str = FALLBACK_WINDOWS_ENVIRONMENT


def current_manual_target(*, ai_dir: Path = TECAN_AI_DIR) -> TargetSetup:
    """Return the current cached-manual target setup."""

    manual = _manual_metadata(ai_dir)
    return TargetSetup(
        fluentcontrol_version=manual.fluentcontrol_version,
        fluentcontrol_build="unknown",
        manual_version=manual.document_version,
        windows_environment=manual.windows_environment,
    )


def build_compatibility_report(
    *,
    ai_dir: Path = TECAN_AI_DIR,
    connector: str = "all",
    target: TargetSetup | None = None,
) -> dict[str, Any]:
    """Build a serializable compatibility matrix from local and public evidence."""

    manual = _manual_metadata(ai_dir)
    tested_builds, tested_build_evidence = _unitelabs_tested_builds(ai_dir)
    connectors = _connector_metadata(ai_dir, manual)
    selected = [
        item
        for item in connectors
        if connector == "all" or item.key == connector
    ]
    if not selected:
        known = ", ".join(item.key for item in connectors)
        raise ValueError(f"unknown connector {connector!r}; expected one of: all, {known}")

    if target is None:
        rows = _default_rows(selected, manual, tested_builds, tested_build_evidence)
    else:
        rows = [
            _classify_target_setup(item, target, manual, tested_builds, tested_build_evidence)
            for item in selected
        ]

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": "tecan.fluent_compatibility_matrix.v2",
        "generated_at": generated_at,
        "local_host": _local_host(),
        "sources": {
            "manual": manual.source_url or SOURCE_MANUAL,
            "windows_compatibility": SOURCE_WINDOWS_COMPATIBILITY,
            "unitelabs_registry": SOURCE_UNITELABS_REGISTRY,
            "unitelabs_gitlab": SOURCE_UNITELABS_GITLAB,
            "tecan_sample_gitlab": SOURCE_TECAN_SAMPLE,
        },
        "manual": manual.as_dict(),
        "connectors": [item.as_dict() for item in selected],
        "known_rules": {
            "current_manual_fluentcontrol_version": manual.fluentcontrol_version,
            "current_manual_version": manual.document_version,
            "current_manual_windows_environment": manual.windows_environment,
            "unitelabs_registry_tested_builds": _sort_builds(tested_builds),
            "unitelabs_registry_tested_builds_evidence": [
                item.as_dict() for item in tested_build_evidence
            ],
        },
        "rows": [row.as_dict() for row in rows],
    }


def render_compatibility_markdown(report: dict[str, Any]) -> str:
    """Render a compatibility matrix report as Markdown."""

    lines = [
        "# FluentControl Version Compatibility Matrix",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Schema: `{report['schema_version']}`",
        "",
        "## Matrix",
        "",
        "| FluentControl | Build | Manual | Connector | Connector version | "
        "SiLA/API compatibility | Windows environment | Tested? | Status | Confidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        cells = [
            row["fluentcontrol_version"],
            row["fluentcontrol_build"],
            row["manual_version"],
            row["connector_name"],
            row["connector_version"],
            row["sila_api_compatibility"],
            row["windows_environment"],
            row["connector_tested"],
            row["status"],
            row["confidence"],
        ]
        lines.append("| " + " | ".join(_table_cell(value) for value in cells) + " |")

    lines.extend(["", "## Extracted Metadata", ""])
    manual = report["manual"]
    lines.extend(
        [
            f"- Manual resource: `{manual['selected_resource']}`",
            f"- Manual FluentControl version: `{manual['fluentcontrol_version']}`",
            f"- Manual document version: `{manual['document_version']}`",
            f"- Manual Windows environment: `{manual['windows_environment']}`",
            "",
        ]
    )
    for connector in report["connectors"]:
        lines.append(f"### {connector['name']}")
        lines.append("")
        lines.append(f"- Package: `{connector['package_name']} {connector['package_version']}`")
        lines.append(f"- Python requirement: `{connector['python_requirement']}`")
        if connector["dependencies"]:
            lines.append(f"- Dependencies: `{', '.join(connector['dependencies'])}`")
        if connector["feature_versions"]:
            lines.append(f"- Feature XML: `{', '.join(connector['feature_versions'])}`")
        if connector["api_references"]:
            lines.append(f"- API references: `{', '.join(connector['api_references'])}`")
        lines.append("")

    lines.extend(["## Local Host", ""])
    local = report["local_host"]
    for key in ("platform", "python", "is_windows", "connector_runtime_note"):
        lines.append(f"- {key}: `{local[key]}`")

    lines.extend(["", "## Evidence", ""])
    for row in report["rows"]:
        lines.extend([f"### {row['connector_name']} / FluentControl {row['fluentcontrol_version']}", ""])
        for item in row["evidence"]:
            lines.append(f"- {_format_evidence(item)}")
        lines.append("")

    lines.extend(["## Sources", ""])
    for name, url in report["sources"].items():
        lines.append(f"- {name}: {url}")

    return "\n".join(lines).rstrip() + "\n"


def _default_rows(
    connectors: list[ConnectorMetadata],
    manual: ManualMetadata,
    tested_builds: set[str],
    tested_build_evidence: tuple[Evidence, ...],
) -> list[CompatibilityRow]:
    rows: list[CompatibilityRow] = []
    current_target = TargetSetup(
        fluentcontrol_version=manual.fluentcontrol_version,
        fluentcontrol_build="unknown",
        manual_version=manual.document_version,
        windows_environment=manual.windows_environment,
    )
    rows.extend(
        _classify_target_setup(connector, current_target, manual, tested_builds, tested_build_evidence)
        for connector in connectors
    )

    for connector in connectors:
        if connector.key != "unitelabs":
            continue
        for build in _sort_builds(tested_builds):
            fluentcontrol_version = _fluentcontrol_label_for_build(build)
            rows.append(
                _classify_target_setup(
                    connector,
                    TargetSetup(
                        fluentcontrol_version=fluentcontrol_version,
                        fluentcontrol_build=build,
                        manual_version=f"FluentControl {fluentcontrol_version} compatibility evidence",
                        windows_environment=LEGACY_WINDOWS_ENVIRONMENT,
                    ),
                    manual,
                    tested_builds,
                    tested_build_evidence,
                )
            )
    return rows


def _classify_target_setup(
    connector: ConnectorMetadata,
    target: TargetSetup,
    manual: ManualMetadata,
    tested_builds: set[str],
    tested_build_evidence: tuple[Evidence, ...],
) -> CompatibilityRow:
    tested = _is_tested_setup(connector, target, tested_builds)
    if tested:
        connector_tested = "Yes - public SiLA registry evidence"
        status = "tested_by_public_registry"
        confidence = "high"
        row_evidence = (
            Evidence(
                claim=f"Unitelabs registry evidence includes FluentControl build {target.fluentcontrol_build}.",
                source="Unitelabs SiLA registry",
                confidence="high",
                url=SOURCE_UNITELABS_REGISTRY,
            ),
            *tested_build_evidence,
        )
    elif connector.key == "unitelabs" and target.fluentcontrol_version == manual.fluentcontrol_version:
        connector_tested = (
            "No - Unitelabs registry evidence only covers FluentControl "
            f"{' and '.join(_sort_builds(tested_builds))}"
        )
        status = "untested_current_manual"
        confidence = "medium"
        row_evidence = (
            Evidence(
                claim=(
                    f"Current cached manual is FluentControl {manual.fluentcontrol_version}, while "
                    f"the captured Unitelabs tested builds are {', '.join(_sort_builds(tested_builds))}."
                ),
                source="Derived from local manual metadata and captured registry builds",
                confidence="medium",
                path=manual.text_path,
                line=_line_for_pattern(Path(manual.text_path), r"399935,\s*en,\s*V(?:ersion\s*)?2\.9"),
            ),
            *manual.evidence,
            *tested_build_evidence,
        )
    else:
        connector_tested = "Unknown - no public tested setup evidence in this workspace"
        status = "needs_local_verification"
        confidence = "low"
        row_evidence = (
            Evidence(
                claim="No matching tested setup was found in the local compatibility evidence.",
                source="Local compatibility classifier",
                confidence="low",
            ),
        )

    return CompatibilityRow(
        connector_key=connector.key,
        connector_name=connector.name,
        fluentcontrol_version=target.fluentcontrol_version,
        fluentcontrol_build=target.fluentcontrol_build,
        manual_version=target.manual_version,
        connector_version=connector.version,
        connector_commit=connector.commit,
        sila_api_compatibility=connector.sila_api_compatibility,
        windows_environment=target.windows_environment or connector.default_windows_environment,
        connector_tested=connector_tested,
        status=status,
        confidence=confidence,
        evidence=(*row_evidence, *connector.evidence),
    )


def _is_tested_setup(connector: ConnectorMetadata, target: TargetSetup, tested_builds: set[str]) -> bool:
    return connector.key == "unitelabs" and target.fluentcontrol_build in tested_builds


def _manual_metadata(ai_dir: Path) -> ManualMetadata:
    manual_dir = (
        ai_dir / "ready-to-import" / "_shared" / "temp_files" / "manuals" / "FluentControl-software-manual"
    )
    source_md = manual_dir / "SOURCE.md"
    source_text = _read_text(source_md)
    source_url = _source_md_value(source_text, "Source page") or SOURCE_MANUAL
    selected_resource = (
        _source_md_value(source_text, "Selected resource")
        or "FluentControl Application Software Manual 3.8 SP1"
    )
    pdf_name = _strip_code_ticks(_source_md_value(source_text, "Downloaded PDF")) or ""
    text_name = _strip_code_ticks(_source_md_value(source_text, "Searchable text companion")) or ""
    retrieved_on = _source_md_value(source_text, "Retrieved on") or "unknown"
    pdf_path = (manual_dir / pdf_name) if pdf_name else manual_dir / "FluentControl-3.8-SP1-Application-Software-Manual.pdf"
    text_path = (
        (manual_dir / text_name)
        if text_name
        else manual_dir / "FluentControl-3.8-SP1-Application-Software-Manual.txt"
    )
    fluentcontrol_version = _extract_fluentcontrol_version(selected_resource, pdf_path) or FALLBACK_MANUAL_FLUENT_VERSION
    document_version = (
        _extract_document_version(source_text)
        or _extract_document_version(_read_text(text_path))
        or FALLBACK_MANUAL_VERSION
    )
    windows_environment = _extract_windows_environment(text_path) or FALLBACK_WINDOWS_ENVIRONMENT

    evidence = (
        Evidence(
            claim=f"Cached manual source selected {selected_resource}.",
            source="Local manual SOURCE.md",
            confidence="high" if source_md.exists() else "low",
            url=source_url,
            path=str(source_md),
            line=_line_for_pattern(source_md, r"Selected resource:"),
        ),
        Evidence(
            claim=f"Manual text identifies document version {document_version}.",
            source="Local manual text extraction",
            confidence="high" if text_path.exists() else "low",
            path=str(text_path),
            line=_line_for_pattern(text_path, r"399935,\s*en,\s*V(?:ersion\s*)?2\.9"),
        ),
        Evidence(
            claim=f"Manual text lists supported Windows environment: {windows_environment}.",
            source="Local manual text extraction",
            confidence="high" if text_path.exists() else "low",
            path=str(text_path),
            line=_line_for_pattern(text_path, r"Windows 11 \(IoT\) Enterprise"),
        ),
    )
    return ManualMetadata(
        selected_resource=selected_resource,
        fluentcontrol_version=fluentcontrol_version,
        document_version=document_version,
        windows_environment=windows_environment,
        source_url=source_url,
        retrieved_on=retrieved_on,
        pdf_path=str(pdf_path),
        text_path=str(text_path),
        evidence=evidence,
    )


def _unitelabs_tested_builds(ai_dir: Path) -> tuple[set[str], tuple[Evidence, ...]]:
    source_md = (
        ai_dir
        / "ready-to-import"
        / "_shared"
        / "temp_files"
        / "manuals"
        / "FluentControl-software-manual"
        / "SOURCE.md"
    )
    source_text = _read_text(source_md)
    builds = set(re.findall(r"\b3\.4\.\d+\.\d+\b", source_text)) or set(FALLBACK_UNITELABS_TESTED_BUILDS)
    evidence = (
        Evidence(
            claim=f"Captured Unitelabs tested FluentControl builds: {', '.join(_sort_builds(builds))}.",
            source="Local manual SOURCE.md compatibility note",
            confidence="medium" if source_md.exists() else "low",
            url=SOURCE_UNITELABS_REGISTRY,
            path=str(source_md),
            line=_line_for_pattern(source_md, r"Compatibility note:"),
        ),
    )
    return builds, evidence


def _connector_metadata(ai_dir: Path, manual: ManualMetadata) -> list[ConnectorMetadata]:
    # Optional local connector checkouts (Inspiration/ is retired).
    connector_root = ai_dir / "ready-to-import" / "_shared" / "temp_files" / "connector-repos"
    unitelabs_root = connector_root / "tecan-fluentcontrol"
    tecan_sample_root = connector_root / "fluent-sila2-connector"
    unit_pkg = _read_pyproject_metadata(
        unitelabs_root / "pyproject.toml",
        fallback=FALLBACK_UNITELABS_PACKAGE,
    )
    unitelabs_commit, unitelabs_commit_date = _git_metadata(unitelabs_root)
    unit_feature_versions = _read_feature_versions(unitelabs_root) or FALLBACK_UNITELABS_FEATURE_VERSIONS
    unit_api_refs = _unitelabs_api_references(unitelabs_root, unit_pkg)

    sample_pkg = _read_pyproject_metadata(tecan_sample_root / "Python" / "pyproject.toml")
    sample_server_version = _read_assembly_version(
        tecan_sample_root / "Server" / "SilaFluentServer" / "Properties" / "AssemblyInfo.cs"
    )
    sample_python_version = _read_python_version(tecan_sample_root / "Python" / "src" / "tecan" / "_version.py")
    sample_commit, sample_commit_date = _git_metadata(tecan_sample_root)
    sample_feature_versions = _read_feature_versions(tecan_sample_root / "Server" / "SilaFluentServer")
    sample_api_refs = _tecan_sample_api_references(tecan_sample_root)

    return [
        ConnectorMetadata(
            key="unitelabs",
            name="Unitelabs Tecan FluentControl connector",
            package_name=unit_pkg["name"],
            package_version=unit_pkg["version"],
            version=_format_package_version(unit_pkg),
            commit=_format_commit(unitelabs_commit, unitelabs_commit_date),
            commit_date=unitelabs_commit_date,
            python_requirement=unit_pkg["requires_python"],
            dependencies=tuple(unit_pkg["dependencies"]),
            package_urls=dict(unit_pkg["urls"]),
            feature_versions=unit_feature_versions,
            api_references=unit_api_refs,
            sila_api_compatibility=_format_sila_api_compatibility(unit_feature_versions, unit_api_refs),
            default_windows_environment=manual.windows_environment,
            evidence=_unitelabs_evidence(unitelabs_root, unit_pkg, unit_feature_versions, unit_api_refs),
        ),
        ConnectorMetadata(
            key="tecan-sample",
            name="Tecan fluent-sila2-connector sample",
            package_name=sample_pkg["name"],
            package_version=sample_python_version,
            version=f"server {sample_server_version}; Python package {sample_pkg['name']} {sample_python_version}",
            commit=_format_commit(sample_commit, sample_commit_date),
            commit_date=sample_commit_date,
            python_requirement=sample_pkg["requires_python"],
            dependencies=tuple(sample_pkg["dependencies"]),
            package_urls=dict(sample_pkg["urls"]),
            feature_versions=sample_feature_versions,
            api_references=sample_api_refs,
            sila_api_compatibility=_format_sila_api_compatibility(sample_feature_versions, sample_api_refs),
            default_windows_environment=manual.windows_environment,
            evidence=_tecan_sample_evidence(
                tecan_sample_root,
                sample_pkg,
                sample_server_version,
                sample_python_version,
                sample_feature_versions,
                sample_api_refs,
            ),
        ),
    ]


def _unitelabs_evidence(
    root: Path,
    package: dict[str, Any],
    feature_versions: tuple[str, ...],
    api_refs: tuple[str, ...],
) -> tuple[Evidence, ...]:
    pyproject = root / "pyproject.toml"
    readme = root / "README.md"
    protocol = root / "src" / "unitelabs" / "tecan_fluentcontrol" / "io" / "fluentcontrol_protocol.py"
    xml_path = _first_feature_xml(root)
    return (
        Evidence(
            claim=f"Package metadata names {package['name']} version {package['version']}.",
            source="Unitelabs pyproject.toml",
            confidence="high" if pyproject.exists() else "low",
            url=package["urls"].get("repository") or SOURCE_UNITELABS_GITLAB,
            path=str(pyproject),
            line=_line_for_pattern(pyproject, r"version\s*="),
        ),
        Evidence(
            claim=f"Package requires Python {package['requires_python']} and dependencies {', '.join(package['dependencies'])}.",
            source="Unitelabs pyproject.toml",
            confidence="high" if pyproject.exists() else "low",
            path=str(pyproject),
            line=_line_for_pattern(pyproject, r"requires-python\s*="),
        ),
        Evidence(
            claim=f"Feature XML declares {', '.join(feature_versions) or 'unknown SiLA feature versions'}.",
            source="Unitelabs feature XML",
            confidence="high" if xml_path and xml_path.exists() else "low",
            path=str(xml_path) if xml_path else None,
            line=_line_for_pattern(xml_path, r"SiLA2Version") if xml_path else None,
        ),
        Evidence(
            claim=f"Runtime/API references detected: {', '.join(api_refs) or 'none'}.",
            source="Unitelabs connector source",
            confidence="high" if protocol.exists() else "low",
            path=str(protocol),
            line=_line_for_pattern(protocol, r"CreateObject\(\"Tecan\.FluentControl\"\)"),
        ),
        Evidence(
            claim="README describes the connector as SiLA 2 1.1 compliant and VisionX COM API based.",
            source="Unitelabs README.md",
            confidence="high" if readme.exists() else "low",
            url=package["urls"].get("documentation") or SOURCE_UNITELABS_GITLAB,
            path=str(readme),
            line=_line_for_pattern(readme, r"SiLA 2 1\.1"),
        ),
    )


def _tecan_sample_evidence(
    root: Path,
    package: dict[str, Any],
    server_version: str,
    python_version: str,
    feature_versions: tuple[str, ...],
    api_refs: tuple[str, ...],
) -> tuple[Evidence, ...]:
    pyproject = root / "Python" / "pyproject.toml"
    version_py = root / "Python" / "src" / "tecan" / "_version.py"
    assembly = root / "Server" / "SilaFluentServer" / "Properties" / "AssemblyInfo.cs"
    readme = root / "README.md"
    csproj = root / "Server" / "SilaFluentController" / "Implementation.csproj"
    xml_path = _first_feature_xml(root / "Server" / "SilaFluentServer")
    return (
        Evidence(
            claim=f"Python package metadata names {package['name']} with runtime version {python_version}.",
            source="Tecan sample Python package metadata",
            confidence="high" if pyproject.exists() and version_py.exists() else "low",
            url=package["urls"].get("Source") or SOURCE_TECAN_SAMPLE,
            path=str(pyproject),
            line=_line_for_pattern(pyproject, r"name\s*="),
        ),
        Evidence(
            claim=f"Server assembly version is {server_version}.",
            source="Tecan sample AssemblyInfo.cs",
            confidence="high" if assembly.exists() else "low",
            path=str(assembly),
            line=_line_for_pattern(assembly, r"AssemblyVersion"),
        ),
        Evidence(
            claim=f"Python package requires {package['requires_python']} and dependencies {', '.join(package['dependencies'])}.",
            source="Tecan sample Python pyproject.toml",
            confidence="high" if pyproject.exists() else "low",
            path=str(pyproject),
            line=_line_for_pattern(pyproject, r"requires-python\s*="),
        ),
        Evidence(
            claim=f"Feature XML declares {', '.join(feature_versions) or 'unknown SiLA feature versions'}.",
            source="Tecan sample feature XML",
            confidence="high" if xml_path and xml_path.exists() else "low",
            path=str(xml_path) if xml_path else None,
            line=_line_for_pattern(xml_path, r"SiLA2Version") if xml_path else None,
        ),
        Evidence(
            claim=f"Runtime/API references detected: {', '.join(api_refs) or 'none'}.",
            source="Tecan sample C# project",
            confidence="high" if csproj.exists() else "low",
            path=str(csproj),
            line=_line_for_pattern(csproj, r"Tecan\.VisionX\.API\.V2"),
        ),
        Evidence(
            claim="README says FluentControl must be installed to build because the project references the default API location.",
            source="Tecan sample README.md",
            confidence="high" if readme.exists() else "low",
            url=SOURCE_TECAN_SAMPLE,
            path=str(readme),
            line=_line_for_pattern(readme, r"FluentControl needs to be installed"),
        ),
    )


def _local_host() -> dict[str, Any]:
    is_windows = os.name == "nt"
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "is_windows": is_windows,
        "connector_runtime_note": (
            "Windows host; connector COM runtime may be checkable if FluentControl is installed."
            if is_windows
            else "Non-Windows host; Unitelabs live COM path is not locally verifiable here."
        ),
    }


def _read_pyproject_metadata(path: Path, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    project = data.get("project", {}) if isinstance(data, dict) else {}
    urls = project.get("urls", {}) if isinstance(project.get("urls", {}), dict) else {}
    dependencies = project.get("dependencies", [])
    fallback = fallback or {}
    return {
        "name": str(project.get("name") or fallback.get("name") or "unknown"),
        "version": str(project.get("version") or fallback.get("version") or "unknown"),
        "requires_python": str(
            project.get("requires-python") or fallback.get("requires_python") or "unknown"
        ),
        "dependencies": (
            tuple(str(item) for item in dependencies)
            if isinstance(dependencies, list) and dependencies
            else tuple(str(item) for item in fallback.get("dependencies", ()))
        ),
        "urls": {
            **{str(key): str(value) for key, value in fallback.get("urls", {}).items()},
            **{str(key): str(value) for key, value in urls.items()},
        },
    }


def _read_python_version(path: Path) -> str:
    text = _read_text(path)
    match = re.search(r"VERSION\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1) if match else "unknown"


def _read_assembly_version(path: Path) -> str:
    text = _read_text(path)
    match = re.search(r'AssemblyVersion\("([^"]+)"\)', text)
    return match.group(1) if match else "unknown"


def _git_metadata(root: Path) -> tuple[str, str]:
    if not (root / ".git").exists():
        return "unknown", "unknown"
    try:
        completed = subprocess.run(
            ["git", "log", "-1", "--format=%H%n%ci"],
            cwd=root,
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return "unknown", "unknown"
    if completed.returncode:
        return "unknown", "unknown"
    lines = completed.stdout.strip().splitlines()
    commit = lines[0] if lines else "unknown"
    date = lines[1] if len(lines) > 1 else "unknown"
    return commit, date


def _read_feature_versions(root: Path) -> tuple[str, ...]:
    versions: set[str] = set()
    for path in sorted(root.rglob("*.sila.xml")):
        text = _read_text(path).lstrip()
        if not text:
            continue
        try:
            element = ET.fromstring(text)
        except ET.ParseError:
            continue
        sila = element.attrib.get("SiLA2Version")
        feature = element.attrib.get("FeatureVersion")
        if sila and feature:
            versions.add(f"SiLA 2 {sila} feature XML v{feature}")
        elif sila:
            versions.add(f"SiLA 2 {sila} feature XML")
    return tuple(sorted(versions))


def _first_feature_xml(root: Path) -> Path | None:
    return next(iter(sorted(root.rglob("*.sila.xml"))), None)


def _unitelabs_api_references(root: Path, package: dict[str, Any]) -> tuple[str, ...]:
    refs: set[str] = set()
    dependencies = [item.lower() for item in package["dependencies"]]
    if any(item.startswith("comtypes") for item in dependencies):
        refs.add("VisionX COM API via comtypes")
    protocol = root / "src" / "unitelabs" / "tecan_fluentcontrol" / "io" / "fluentcontrol_protocol.py"
    text = _read_text(protocol)
    if "CreateObject(\"Tecan.FluentControl\")" in text:
        refs.add("Tecan.FluentControl COM object")
    if "GetModule((\"{86977DF6-167E-4684-AC6B-672CBE095C9B}\"" in text:
        refs.add("FluentControl COM type library GUID 86977DF6-167E-4684-AC6B-672CBE095C9B")
    return tuple(sorted(refs))


def _tecan_sample_api_references(root: Path) -> tuple[str, ...]:
    refs: set[str] = set()
    for path in (
        root / "Server" / "SilaFluentController" / "Implementation.csproj",
        root / "Server" / "SilaFluentServer" / "SilaFluentServer.csproj",
    ):
        text = _read_text(path)
        if "Tecan.VisionX.API.V2" in text:
            refs.add("Tecan.VisionX.API.V2")
        if "Program Files (x86)\\Tecan\\FluentControl" in text:
            refs.add("default FluentControl install API path")
    return tuple(sorted(refs))


def _format_sila_api_compatibility(feature_versions: tuple[str, ...], api_refs: tuple[str, ...]) -> str:
    feature_text = ", ".join(feature_versions) if feature_versions else "unknown SiLA feature XML"
    api_text = ", ".join(api_refs) if api_refs else "no local API reference detected"
    return f"{feature_text}; {api_text}"


def _format_package_version(package: dict[str, Any]) -> str:
    if package["name"] == "unknown" and package["version"] == "unknown":
        return "unknown"
    if package["version"] == "unknown":
        return package["name"]
    return f"{package['name']} {package['version']}"


def _format_commit(commit: str, commit_date: str) -> str:
    if commit == "unknown":
        return "unknown"
    short = commit[:12]
    return f"{short} ({commit_date})" if commit_date != "unknown" else short


def _sort_builds(builds: set[str]) -> list[str]:
    return sorted(builds, key=lambda value: tuple(int(part) for part in value.split(".") if part.isdigit()))


def _fluentcontrol_label_for_build(build: str) -> str:
    parts = build.split(".")
    if len(parts) < 2:
        return build
    base = ".".join(parts[:2])
    if base == "3.4" and len(parts) > 2 and parts[2] == "10":
        return "3.4 SP1"
    return base


def _extract_fluentcontrol_version(selected_resource: str, pdf_path: Path) -> str | None:
    for value in (selected_resource, pdf_path.name):
        normalized = value.replace("-", " ")
        match = re.search(r"\b(\d+(?:\.\d+)+(?:\s+SP\d+)?)\b", normalized, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).upper().replace("SP", "SP")
    return None


def _extract_document_version(text: str) -> str | None:
    match = re.search(r"\b(399935),\s*en,\s*V(?:ersion\s*)?(\d+)\.(\d+)\b", text, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1)}_en_V{match.group(2)}_{match.group(3)}"
    match = re.search(r"`([^`]*399935[^`]*)`", text)
    return match.group(1) if match else None


def _extract_windows_environment(text_path: Path) -> str | None:
    text = _read_text(text_path)
    match = re.search(
        r"Windows 11 \(IoT\) Enterprise\s+LTSC 2024,?\s+or Windows 10 \(IoT\) Enterprise\s+LTSC\s*2021",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(0)).replace(",", "")


def _source_md_value(text: str, label: str) -> str | None:
    match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _strip_code_ticks(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip("`")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    except OSError:
        return ""


def _line_for_pattern(path: Path, pattern: str) -> int | None:
    if path is None:
        return None
    text = _read_text(path)
    if not text:
        return None
    regex = re.compile(pattern)
    for index, line in enumerate(text.splitlines(), start=1):
        if regex.search(line):
            return index
    return None


def _format_evidence(item: dict[str, Any]) -> str:
    source_parts: list[str] = []
    if item.get("url"):
        source_parts.append(_markdown_link(item["source"], item["url"]))
    if item.get("path"):
        label = Path(item["path"]).name
        if item.get("line") is not None:
            label = f"{label}:{item['line']}"
        target = item["path"] + (f":{item['line']}" if item.get("line") is not None else "")
        source_parts.append(_markdown_link(label, target))
    source = "; ".join(source_parts) if source_parts else item["source"]
    return f"`{item['confidence']}` {item['claim']} Source: {source}"


def _markdown_link(label: str, target: str) -> str:
    if re.match(r"^[a-z]+://", target):
        return f"[{label}]({target})"
    escaped = target.replace(">", "%3E")
    return f"[{label}](<{escaped}>)"


def _table_cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text
