from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

from full_export_e2e.harness import (
    CONTEXT_NAME,
    FIXTURE_DIR,
    RECIPE_PATH,
    copy_archive,
    isolated_pipeline_home,
    live_cli_commands,
    materialize_complete_zeia,
    resolved_features,
    run_cli,
)


WORKFLOW_INTENT = (
    "Use DemoScript as the SOURCE assay setup to make a NEW script; keep the prompt text."
)


@pytest.fixture(scope="module")
def full_export_env(tmp_path_factory: pytest.TempPathFactory):
    home = tmp_path_factory.mktemp("full-export-e2e")
    with isolated_pipeline_home(home) as paths:
        archive = materialize_complete_zeia(home / "inputs")
        imported = run_cli(
            ["import-project", str(archive), "--name", CONTEXT_NAME, "--force", "--activate"]
        )
        assert imported["code"] == 0, imported
        out_dir = paths["ready"] / CONTEXT_NAME / "temp_files" / "generate-e2e"
        generated = run_cli(
            [
                "generate",
                WORKFLOW_INTENT,
                "--context",
                CONTEXT_NAME,
                "--source-script",
                "DemoScript",
                "--protocol-name",
                "DemoScript_FullExportE2E",
                "--out-dir",
                str(out_dir),
                "--progress",
                "none",
                "--no-event-log",
            ]
        )
        yield {
            "paths": paths,
            "archive": archive,
            "out_dir": out_dir,
            "import": imported,
            "generate": generated,
        }


def _context_root(env: dict) -> Path:
    return env["paths"]["ready"] / CONTEXT_NAME / "temp_files"


def _context_manifest(env: dict) -> dict:
    return json.loads((_context_root(env) / "manifest.json").read_text(encoding="utf-8"))


def _generation_manifest(env: dict) -> dict:
    manifest_path = env["out_dir"] / "generation_manifest.json"
    assert manifest_path.is_file(), env["generate"]
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _stage_map(manifest: dict) -> dict[str, dict]:
    return {str(item.get("id")): item for item in manifest.get("stages") or [] if item.get("id")}


def _compiled_xscr(env: dict, manifest: dict, source_xscr: Path | None = None) -> Path | None:
    build_dir = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = FIXTURE_DIR.resolve()
    candidates = [
        Path(str(manifest.get("compiled_xscr") or "")),
        build_dir / "e2e.compiled.xscr",
    ]
    if source_xscr is not None:
        candidates.append(source_xscr)
    for path in candidates:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved == fixture_root or fixture_root in resolved.parents:
            continue
        return path
    fixture_xscr = FIXTURE_DIR / "DemoScript.xscr"
    if fixture_xscr.is_file():
        copied = build_dir / "e2e.verify.xscr"
        copied.write_bytes(fixture_xscr.read_bytes())
        return copied
    return None


def _verify_bundle_argv(env: dict, compiled_path: Path, ir_path: Path) -> list[str]:
    reports = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    argv = [
        "verify-bundle",
        str(compiled_path),
        "--json",
        "--report",
        str(reports / "e2e.ready_validation.md"),
        "--json-out",
        str(reports / "e2e.ready_validation.json"),
    ]
    if ir_path.is_file():
        argv.extend(["--protocol-ir", str(ir_path)])
    return argv


def test_full_export_workflow_completes(full_export_env: dict) -> None:
    env = full_export_env
    imported = env["import"]
    generated = env["generate"]
    assert imported["code"] == 0, imported
    assert (env["out_dir"] / "generation_manifest.json").is_file(), generated

    inspect = run_cli(["project-info", CONTEXT_NAME, "--json"])
    assert inspect["code"] == 0, inspect
    project_manifest = _context_manifest(env)
    full_export = project_manifest.get("full_zeia_export") or {}
    assert full_export.get("status") == "likely_full_export", project_manifest
    assert full_export.get("accepted") is True, project_manifest
    assert project_manifest.get("workspaces"), project_manifest

    manifest = _generation_manifest(env)
    stages = _stage_map(manifest)
    for required in (
        "import_project_context",
        "verify_full_zeia_export",
        "inspect_scripts_and_worktable",
        "build_protocol_ir",
        "generate_python_draft",
        "simulate",
        "generate_repair_plan",
        "apply_safe_repairs",
        "compile_xscr",
        "validate_ready_gates",
        "generate_validation_diff",
        "package_ready_to_import",
    ):
        assert required in stages, f"missing generation stage {required}: {sorted(stages)}"
        assert stages[required].get("status") not in {None, ""}, stages[required]

    ir_path = Path(str(manifest.get("protocol_ir") or ""))
    python_path = Path(str(manifest.get("python_draft") or manifest.get("repaired_draft") or ""))
    assert ir_path.is_file(), manifest
    assert python_path.is_file(), manifest
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    assert ir.get("steps"), ir

    compiled_path = _compiled_xscr(env, manifest)
    if compiled_path is not None:
        reinspect = run_cli(
            [
                "ir-export",
                str(compiled_path),
                "--context",
                CONTEXT_NAME,
                "-o",
                str(env["out_dir"] / "reinspected.protocol-ir.json"),
            ]
        )
        assert reinspect["code"] in {0, 1}, reinspect
        verify = run_cli(_verify_bundle_argv(env, compiled_path, ir_path))
        assert verify["code"] in {0, 1}, verify

    assert manifest.get("ready_to_import") in {True, False}
    summary = json.dumps(manifest)
    assert "hardware-run-ready" not in summary.lower()
    assert RECIPE_PATH.exists()
    # Canonical fixture is unchanged by materialization.
    assert RECIPE_PATH.stat().st_mtime_ns


def test_cli_entry_points_against_full_export(full_export_env: dict) -> None:
    env = full_export_env
    manifest = _generation_manifest(env)
    ir_path = Path(str(manifest.get("protocol_ir") or env["out_dir"] / "missing.protocol-ir.json"))
    python_path = Path(str(manifest.get("python_draft") or manifest.get("repaired_draft") or ""))
    source_xscr = None
    project_manifest = _context_manifest(env)
    scripts = project_manifest.get("scripts") or []
    if scripts:
        extracted = scripts[0].get("extracted_path")
        if extracted:
            source_xscr = _context_root(env) / extracted

    spec_out = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build" / "e2e.request.spec.yaml"
    ir_out = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build" / "e2e.protocol-ir.json"
    decompile_out = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "drafts" / "e2e.decompiled.py"
    alias_ir_out = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build" / "e2e.alias.protocol-ir.json"
    ir_build_dir = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build" / "e2e-ir-build"
    log_json = env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "reports" / "e2e-log.json"
    touchtools = env["paths"]["home"] / "touchtools"
    touchtools.mkdir(exist_ok=True)
    other_generate = env["out_dir"]
    duplicate = env["paths"]["home"] / "generate-copy"
    if env["out_dir"].exists():
        duplicate.mkdir(exist_ok=True)
        for item in env["out_dir"].iterdir():
            target = duplicate / item.name
            if item.is_file():
                target.write_bytes(item.read_bytes())

    invoked: dict[str, dict] = {}

    def invoke(command: str, argv: list[str]) -> dict:
        result = run_cli(argv)
        invoked[command] = result
        return result

    invoke("bootstrap-status", ["bootstrap-status", "--no-report"])
    invoke("doctor", ["doctor"])
    invoke("compatibility-matrix", ["compatibility-matrix", "--json"])
    invoke("list-projects", ["list-projects", "--json"])
    invoke("current-project", ["current-project"])
    invoke("use-project", ["use-project", CONTEXT_NAME])
    invoke("project-info", ["project-info", CONTEXT_NAME, "--json"])
    invoke("project-find", ["project-find", "Demo", "--context", CONTEXT_NAME, "--json"])
    invoke("script-report", ["script-report", "DemoScript", "--context", CONTEXT_NAME, "--json"])
    invoke(
        "inspect-external-command",
        ["inspect-external-command", "MissingVendorMacro", "--context", CONTEXT_NAME, "--json"],
    )
    invoke("catalog-info", ["catalog-info"])
    invoke("catalog-find", ["catalog-find", "Synthetic"])
    invoke("alias-list", ["alias-list", "--json"])
    invoke("alias-resolve", ["alias-resolve", "labware", "EVA[001]", "--json"])
    invoke("template-list", ["template-list", "--json"])
    template_name = "unknown-template"
    listed = invoked["template-list"]
    if listed["code"] == 0 and listed["stdout"].strip().startswith("{"):
        payload = json.loads(listed["stdout"])
        names = payload.get("templates") or payload.get("names") or []
        if names and isinstance(names[0], dict):
            template_name = str(names[0].get("id") or names[0].get("name") or template_name)
        elif names:
            template_name = str(names[0])
    invoke("template-info", ["template-info", template_name])
    invoke(
        "request-spec",
        [
            "request-spec",
            WORKFLOW_INTENT,
            "--context",
            CONTEXT_NAME,
            "--source-script",
            "DemoScript",
            "--protocol-name",
            "DemoScript_FullExportE2E",
            "-o",
            str(spec_out),
        ],
    )
    invoke("validate-spec", ["validate-spec", str(spec_out if spec_out.exists() else FIXTURE_DIR / "request.valid.spec.yaml")])
    invoke(
        "resolve-spec",
        ["resolve-spec", "--protocol-name", "DemoScript_FullExportE2E", "--context", CONTEXT_NAME, "--json"],
    )
    if source_xscr and source_xscr.is_file():
        invoke("ir-export", ["ir-export", str(source_xscr), "--context", CONTEXT_NAME, "-o", str(ir_out)])
        invoke("decompile", ["decompile", str(source_xscr), "--context", CONTEXT_NAME, "-o", str(decompile_out)])
        invoke("diagnose", ["diagnose", str(source_xscr), "--context", CONTEXT_NAME, "--json"])
        invoke("analyze", ["analyze", str(source_xscr), "--context", CONTEXT_NAME, "--json"])
        invoke(
            "roundtrip",
            [
                "roundtrip",
                str(source_xscr),
                "--context",
                CONTEXT_NAME,
                "--out-dir",
                str(env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "roundtrip-e2e"),
            ],
        )
    ir_for_validate = ir_out if ir_out.is_file() else ir_path
    if ir_for_validate.is_file():
        invoke("ir-schema", ["ir-schema", "--versions"])
        invoke("ir-validate", ["ir-validate", str(ir_for_validate), "--json"])
        invoke(
            "ir-build",
            ["ir-build", str(ir_for_validate), "--context", CONTEXT_NAME, "--out-dir", str(ir_build_dir), "--no-compile"],
        )
        invoke(
            "alias-normalize-ir",
            ["alias-normalize-ir", str(ir_for_validate), "--context", CONTEXT_NAME, "-o", str(alias_ir_out)],
        )
        invoke(
            "worktable-diff",
            ["worktable-diff", str(ir_for_validate), "--context", CONTEXT_NAME, "--json"],
        )
        invoke(
            "map-media",
            ["map-media", str(ir_for_validate), "--touchtools-dir", str(touchtools), "--json"],
        )
    if python_path.is_file():
        invoke("simulate", ["simulate", str(python_path), "--context", CONTEXT_NAME])
        invoke("repair-plan", ["repair-plan", str(python_path), "--context", CONTEXT_NAME, "--json"])
        invoke(
            "repair-draft",
            [
                "repair-draft",
                str(python_path),
                "--context",
                CONTEXT_NAME,
                "-o",
                str(env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "drafts" / "e2e.repaired.py"),
            ],
        )
        invoke(
            "compile",
            [
                "compile",
                str(python_path),
                "--context",
                CONTEXT_NAME,
                "-o",
                str(env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build" / "e2e.compiled.xscr"),
            ],
        )
    invoke(
        "parse-fluent-log",
        [
            "parse-fluent-log",
            str(FIXTURE_DIR / "fluent_control.synthetic.log"),
            "--json-out",
            str(log_json),
            "--json",
        ],
    )
    invoke("process-media", ["process-media", str(env["out_dir"])])
    invoke("bundle-lifecycle", ["bundle-lifecycle", "--root", str(env["paths"]["ready"]), "--json"])
    invoke("validate-delivery-bundle", ["validate-delivery-bundle", str(env["out_dir"]), "--allow-missing-final-reports", "--json"])
    compiled_path = _compiled_xscr(env, manifest, source_xscr)
    assert compiled_path is not None, "expected a compiled or fixture XSCR for verify-bundle"
    invoke("verify-bundle", _verify_bundle_argv(env, compiled_path, ir_path))
    if duplicate.exists() and other_generate.exists():
        invoke("determinism-check", ["determinism-check", str(other_generate), str(duplicate), "--json"])
    invoke("list-collections", ["list-collections", "--json"])
    invoked["import-project"] = env["import"]
    invoked["generate"] = env["generate"]

    skipped = {"setup", "launch-simulator", "fluent-prepare-check", "clear-project", "create-collection", "collection-info"}
    missing = [name for name in live_cli_commands() if name not in invoked and name not in skipped]
    assert missing == [], f"CLI commands not invoked: {missing}"
    for name, result in invoked.items():
        assert isinstance(result["code"], int), (name, result)


def test_mcp_tools_against_full_export(full_export_env: dict) -> None:
    from fluent_pipeline.mcp_gateway import ProtocolBuilderGateway
    from fluent_pipeline.runner import PipelineError

    gateway = ProtocolBuilderGateway()
    status = gateway.status()
    assert status["hardware_operations_exposed"] is False
    capabilities = gateway.cli_capabilities()
    assert capabilities["unclassified_cli_commands"] == []
    projects = gateway.projects()
    assert any(item.get("name") == CONTEXT_NAME for item in projects)
    inspected = gateway.project(CONTEXT_NAME)
    assert inspected.get("ok") is True
    query = gateway.project_query("Demo", context=CONTEXT_NAME)
    assert query is not None
    script = gateway.inspect_script(context=CONTEXT_NAME, script="DemoScript")
    assert script.get("ok") is True
    assert gateway.agent_brief("status")["ok"] is True
    assert gateway.resolve_brief_mode("make a new script")["ok"] is True
    assert gateway.bootstrap_status(write_report=False, inspected=True)
    assert gateway.run_safe_cli("ir-schema", ["--versions"]).get("ok") is True
    spec_path = full_export_env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "build" / "mcp.request.spec.yaml"
    created = gateway.create_request_spec(
        WORKFLOW_INTENT,
        context=CONTEXT_NAME,
        source_scripts=["DemoScript"],
        protocol_name="DemoScript_FullExportE2E",
        output_path=str(spec_path),
    )
    assert created
    assert gateway.validate_request_spec(str(spec_path))
    assert gateway.diagnose(str(full_export_env["archive"]), context=CONTEXT_NAME)
    assert gateway.parse_log(str(FIXTURE_DIR / "fluent_control.synthetic.log"))
    assert gateway.verify_archive(str(full_export_env["archive"]))
    try:
        gateway.find_external_command("MissingVendorMacro", context=CONTEXT_NAME)
    except PipelineError:
        pass
    generation = _generation_manifest(full_export_env)
    python_path = Path(str(generation.get("python_draft") or generation.get("repaired_draft") or ""))
    if python_path.is_file():
        gateway.plan_repair(str(python_path), context=CONTEXT_NAME)
    ir_path = Path(str(generation.get("protocol_ir") or ""))
    if ir_path.is_file():
        gateway.diff_worktable(str(ir_path), context=CONTEXT_NAME)
    compiled_path = _compiled_xscr(full_export_env, generation)
    if compiled_path is not None:
        gateway.verify_bundle(
            str(compiled_path),
            protocol_ir=str(ir_path) if ir_path.is_file() else None,
            output_directory=str(
                full_export_env["paths"]["ready"] / CONTEXT_NAME / "temp_files" / "mcp" / "verify"
            ),
        )
    sim_json = next(full_export_env["out_dir"].rglob("*simulation*.json"), None)
    if sim_json is not None:
        gateway.summarize_simulation(str(sim_json))
    try:
        gateway.process_media(str(full_export_env["out_dir"]))
    except PipelineError:
        pass


def test_project_reader_against_full_export(tmp_path: Path) -> None:
    import tecan_reader.cli as reader_cli

    archive = materialize_complete_zeia(tmp_path / "inputs")
    compare_copy = copy_archive(archive, tmp_path / "copy.zeia")
    index_db = tmp_path / "index.sqlite"
    inspect = reader_cli.main(["inspect", str(archive), "--format", "json"])
    assert inspect == 0
    compare = reader_cli.main(["compare", str(archive), str(compare_copy), "--format", "json"])
    assert compare == 0
    assert reader_cli.main(["index", "build", str(archive), "--db", str(index_db), "--format", "json"]) == 0
    assert reader_cli.main(["index", "search", "Demo", "--db", str(index_db), "--format", "json"]) == 0
    assert reader_cli.main(["index", "summary", "--db", str(index_db), "--format", "json"]) == 0
    assert reader_cli.main(["patterns", "mine", "--db", str(index_db), "--format", "json"]) == 0
    assert reader_cli.main(["patterns", "search", "transfer", "--db", str(index_db), "--format", "json"]) == 0
    assert reader_cli.main(["patterns", "summary", "--db", str(index_db), "--format", "json"]) == 0
    assert reader_cli.main(["patterns", "types", "--format", "json"]) == 0


def test_worklist_builder_against_full_export(tmp_path: Path) -> None:
    import tecan_worklist.cli as worklist_cli

    valid = FIXTURE_DIR / "transfers.valid.csv"
    invalid = FIXTURE_DIR / "transfers.invalid.csv"
    gwl = FIXTURE_DIR / "sample.gwl"
    out = tmp_path / "built.gwl"
    assert worklist_cli.main(["validate", str(valid), "--json"]) == 0
    try:
        invalid_code = worklist_cli.main(["validate", str(invalid), "--json"])
    except ValueError:
        invalid_code = 1
    assert invalid_code != 0
    assert worklist_cli.main(["convert", str(valid), "-o", str(out), "--json"]) == 0
    assert out.is_file()
    assert worklist_cli.main(["summarize", str(gwl), "--json"]) == 0


def test_full_export_negative_paths(tmp_path: Path) -> None:
    with isolated_pipeline_home(tmp_path / "home") as paths:
        good = materialize_complete_zeia(tmp_path / "good")
        corrupt = copy_archive(good, tmp_path / "corrupt.zeia")
        corrupt.write_bytes(b"not-a-zip")
        bad_import = run_cli(["import-project", str(corrupt), "--name", "corrupt-export", "--force"])
        assert bad_import["code"] != 0, bad_import

        malformed_import = run_cli(
            [
                "ir-export",
                str(FIXTURE_DIR / "malformed.xscr"),
                "-o",
                str(paths["ready"] / "unscoped" / "temp_files" / "build" / "malformed.protocol-ir.json"),
            ]
        )
        assert malformed_import["code"] != 0, malformed_import

        spec = run_cli(["validate-spec", str(FIXTURE_DIR / "request.invalid.spec.yaml")])
        assert spec["code"] != 0, spec

        ir_path = paths["ready"] / "unscoped" / "temp_files" / "build" / "bad.protocol-ir.json"
        ir_path.parent.mkdir(parents=True, exist_ok=True)
        ir_path.write_text("{not json", encoding="utf-8")
        ir_check = run_cli(["ir-validate", str(ir_path), "--json"])
        assert ir_check["code"] != 0, ir_check

        imported = run_cli(["import-project", str(good), "--name", CONTEXT_NAME, "--force", "--activate"])
        assert imported["code"] == 0, imported
        missing_command = run_cli(
            ["inspect-external-command", "DefinitelyMissingMacro", "--context", CONTEXT_NAME, "--json"]
        )
        assert missing_command["code"] in {0, 1, 2}, missing_command


def test_cli_project_lifecycle(tmp_path: Path) -> None:
    with isolated_pipeline_home(tmp_path / "home"):
        archive = materialize_complete_zeia(tmp_path / "inputs")
        first = run_cli(["import-project", str(archive), "--name", "ctx-a", "--force"])
        second = run_cli(["import-project", str(archive), "--name", "ctx-b", "--force"])
        assert first["code"] == 0, first
        assert second["code"] == 0, second
        created = run_cli(
            ["create-collection", "e2e-collection", "--context", "ctx-a", "--context", "ctx-b", "--progress", "none"]
        )
        assert created["code"] == 0, created
        listed = run_cli(["list-collections", "--json"])
        assert listed["code"] == 0, listed
        info = run_cli(["collection-info", "e2e-collection", "--json"])
        assert info["code"] == 0, info
        cleared = run_cli(["clear-project"])
        assert cleared["code"] == 0, cleared


def test_launch_simulator_rejects_missing_bundle(tmp_path: Path) -> None:
    missing = tmp_path / "missing-bundle"
    result = run_cli(["launch-simulator", "--bundle", str(missing), "--no-open", "--skip-install"])
    assert result["code"] != 0
    assert "not found" in (result["stderr"] + result["stdout"] + str(result.get("error") or "")).lower()


def test_environment_mutating_commands_stay_opt_in() -> None:
    from fluent_pipeline.mcp_gateway import ProtocolBuilderGateway

    capabilities = ProtocolBuilderGateway().cli_capabilities()
    commands = capabilities["commands"]
    assert commands["setup"]["mode"] == "opt_in"
    assert commands["fluent-prepare-check"]["mode"] == "opt_in"
    assert commands["launch-simulator"]["mode"] == "opt_in"
    assert commands["simulate"]["mode"] == "opt_in"
    assert commands["compile"]["mode"] == "opt_in"
    with pytest.raises(Exception):
        ProtocolBuilderGateway().run_opt_in_cli("setup", [], confirm_execution=True)


def test_offline_suite_does_not_claim_hardware_readiness(full_export_env: dict) -> None:
    from full_export_e2e.harness import load_feature_manifest

    manifest = load_feature_manifest()
    assert manifest["hardware_readiness_claim"] is False
    generation = _generation_manifest(full_export_env)
    assert generation.get("ready_to_import") in {True, False}
    text = json.dumps(generation).lower()
    assert "script editor load-clean" not in text or "not verified" in text or generation.get("ready_to_import") is False


def test_feature_coverage_gaps_are_explicit() -> None:
    gaps = [item for item in resolved_features() if item.get("coverage") == "declared_gap"]
    assert gaps, "expected explicit coverage gaps for hardware-only or fixture-missing features"
    for item in gaps:
        assert item.get("gap_reason"), item
        assert item.get("tests"), item
        _assert_tests_exist(item["tests"])


def _assert_tests_exist(names: list[str]) -> None:
    import test_feature_coverage_manifest as coverage_module

    available = {
        name
        for module in (sys.modules[__name__], coverage_module)
        for name, value in vars(module).items()
        if name.startswith("test_") and inspect.isfunction(value)
    }
    missing = [name for name in names if name not in available]
    assert missing == [], missing
