from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.cli import main  # noqa: E402


def test_simulate_json_includes_coverage(tmp_path: Path, capsys) -> None:
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli")
    wt.group("Steps")
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--json", "--coverage"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_executed_steps"] == 1
    assert payload["validation_only_steps"] == 1
    assert payload["modeled_coverage"] == 1.0
    assert payload["snapshots"][0]["step_type"] == "WaitStep"


def test_simulate_fail_on_opaque_exits_nonzero(tmp_path: Path, capsys) -> None:
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli opaque")
    wt.group("Steps")
    wt.generic_step("UnknownProductionCommand")
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--fail-on-opaque"]) == 1
    captured = capsys.readouterr()
    assert "opaque" in captured.err


def test_simulate_min_coverage_exits_nonzero(tmp_path: Path, capsys) -> None:
    """--min-coverage flag exits nonzero when coverage is below threshold."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli low cov")
    wt.group("Steps")
    wt.wait(1)
    wt.generic_step("UnknownCmd")
    return wt
""",
        encoding="utf-8",
    )

    # Coverage is 0.5 (wait=validation_only, unknown=opaque) → below 0.75
    assert main(["simulate", str(protocol), "--min-coverage", "0.75"]) == 1
    captured = capsys.readouterr()
    assert "below" in captured.err or "coverage" in captured.err.lower()


def test_simulate_strict_json_failure_includes_status_and_failure(tmp_path: Path, capsys) -> None:
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli strict")
    wt.group("Steps")
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--strict", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["failure"]["category"] == "workspace_binding"


def test_simulate_strict_text_failure_reports_category(tmp_path: Path, capsys) -> None:
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli strict text")
    wt.group("Steps")
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--strict"]) == 1
    captured = capsys.readouterr()
    assert "workspace_binding" in captured.err


def test_simulate_text_mode_no_coverage_block(tmp_path: Path, capsys) -> None:
    """Without --coverage flag, no coverage block appears in text output."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli text")
    wt.group("Steps")
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol)]) == 0
    output = capsys.readouterr().out
    # Text mode should show step lines but NOT the coverage block
    assert "step" in output.lower() or "WaitStep" in output
    assert "Coverage:" not in output
    assert "modeled coverage" not in output.lower()


def test_simulate_text_mode_coverage_block(tmp_path: Path, capsys) -> None:
    """With --coverage flag, coverage block appears in text output."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli cov")
    wt.group("Steps")
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--coverage"]) == 0
    output = capsys.readouterr().out
    assert "Coverage:" in output
    assert "modeled coverage: 1.000" in output.lower() or "modeled_coverage" not in output


def test_simulate_json_validates_report_and_snapshot_fields(tmp_path: Path, capsys) -> None:
    """JSON payload contains both report fields and snapshot fields."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable, Plate96, Reagent

def build_worktable():
    wt = Worktable(name="cli json full")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Water"), 20.0)
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    # Report fields exist
    assert "total_executed_steps" in payload
    assert "fully_simulated_steps" in payload
    assert "validation_only_steps" in payload
    assert "opaque_noop_steps" in payload
    assert "modeled_coverage" in payload
    assert "unsupported_command_ids" in payload
    assert "steps" in payload
    assert "warnings" in payload

    # Snapshot fields exist
    assert "snapshots" in payload
    assert len(payload["snapshots"]) >= 2  # AddLabware + Wait at minimum

    # Final labware and tip summaries exist
    assert "final_labware" in payload
    assert "Source" in payload["final_labware"]
    assert "final_mca_tips" in payload
    assert "final_liha_tips" in payload

    # Snapshot has expected fields
    snap = payload["snapshots"][0]
    assert "step_index" in snap
    assert "step_type" in snap
    assert "labware" in snap
    assert "mca_adapter" in snap
    assert "mca_tip_volume_total_ul" in snap
    assert "liha_tip_volume_total_ul" in snap


def test_simulate_warnings_print_in_text_mode(tmp_path: Path, capsys) -> None:
    """Warnings appear in text output when present."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli warnings")
    wt.group("Steps")
    # Unknown command triggers a warning about opaque steps
    wt.generic_step("UnknownCmd1")
    wt.generic_step("UnknownCmd2")
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--coverage"]) == 0
    output = capsys.readouterr().out
    # With --coverage, warnings and unsupported commands should print
    assert "warning" in output.lower() or "unsupported" in output.lower()


def test_simulate_unsupported_command_summary(tmp_path: Path, capsys) -> None:
    """Unsupported command summary prints when opaque steps exist."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable

def build_worktable():
    wt = Worktable(name="cli unsupported")
    wt.group("Steps")
    wt.generic_step("BadCmdA")
    wt.generic_step("BadCmdB")
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--coverage"]) == 0
    output = capsys.readouterr().out
    # Unsupported command summary should list the opaque commands
    assert "BadCmdA" in output or "unsupported" in output.lower()


def test_simulate_json_with_liha_summaries(tmp_path: Path, capsys) -> None:
    """JSON payload includes LiHa volume summaries when applicable."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable, Plate96, Reagent

def build_worktable():
    wt = Worktable(name="cli liha")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    # LiHa operation
    wt.liha.get_tips()
    wt.liha.aspirate(src, 5.0, liquid_class="Water Free Single")

    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    # LiHa tip volume should be reflected in snapshots
    last_snap = payload["snapshots"][-1]
    assert last_snap["liha_tip_volume_total_ul"] > 0

    # final_liha_tips should have non-None entries for channels with tips
    liha_final = payload["final_liha_tips"]
    assert any(t is not None and t["volume_ul"] > 0 for t in liha_final)


def test_simulate_json_with_mca_summaries(tmp_path: Path, capsys) -> None:
    """JSON payload includes MCA volume summaries when applicable."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable, Plate96, Reagent

def build_worktable():
    wt = Worktable(name="cli mca")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Buffer"), 50.0)

    # MCA operation — mount adapter, get tips via raw XML (populates 384-tip array),
    # then aspirate to load liquid into those tips.
    wt.mca96.mount_adapter()
    wt.raw_xml_step("Mca384GetTips", "<Object><LabwareName>Tips</LabwareName></Object>")
    wt.mca96.aspirate(src, 10.0, liquid_class="Water Free Single")

    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    # MCA tip volume should be reflected in snapshots
    last_snap = payload["snapshots"][-1]
    assert last_snap["mca_tip_volume_total_ul"] > 0

    # final_mca_tips should have entries with volume for tips that aspirated
    mca_final = payload["final_mca_tips"]
    assert any(t["volume_ul"] > 0 for t in mca_final)


def test_simulate_delta_snapshots_records_per_step_without_slot_maps(
    tmp_path: Path, capsys
) -> None:
    """--delta-snapshots stores per-step history without deep-copied slot maps."""
    protocol = tmp_path / "protocol.py"
    protocol.write_text(
        """
from fluentcoder import Worktable, Plate96, Reagent

def build_worktable():
    wt = Worktable(name="cli delta")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.fill_all(Reagent("Water"), 20.0)
    wt.wait(1)
    return wt
""",
        encoding="utf-8",
    )

    assert main(["simulate", str(protocol), "--json", "--delta-snapshots"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["snapshots"]) >= 2
    assert all(not snap["labware"] for snap in payload["snapshots"])
    assert "Source" in payload["final_labware"]

