"""Optional private golden regression for a lab verification script.

Public clones: helper unit tests only (synthetic XSCR fragments).
Heavy regenerate+assert runs only when explicitly opted in with private paths.

  export TECAN_ENABLE_PRIVATE_GOLDENS=1
  export TECAN_GOLDEN_SPEC=/path/to/private/request.spec.yaml
  export TECAN_GOLDEN_CONTEXT=<imported-context-name>
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

from fluent_pipeline.generation_options import GenerationOptions, normalize_generation_options
from fluent_pipeline.generation_workflow import ApprovalSet, GenerationRequest, run_generation_workflow as _run_generation_workflow
from fluent_pipeline.project_context import load_project
from fluent_pipeline.request_spec import load_request_spec, write_request_spec
from fluent_pipeline.validation import _gate_subroutine_calls_resolve

from golden.verification_v12_xscr_checks import (
    assert_all_verification_v12_golden_xscr_checks,
    assert_capbc_input_sub_location_literals,
    assert_gripper_prep_defaults,
    assert_rup_configure_data_labware_booleans_are_lowercase,
)

_TRUTHY = {"1", "true", "yes", "on"}


def run_generation_workflow(**kwargs):
    request_spec = kwargs.pop("request_spec", None)
    request_spec_source = kwargs.pop("request_spec_source", None)
    options = dataclass_replace(
        normalize_generation_options(
            kwargs.pop("options", GenerationOptions()),
            simulate=kwargs.pop("simulate", None),
            compile_xscr=kwargs.pop("compile_xscr", None),
        ),
    )
    if request_spec is not None and request_spec_source is not None:
        request_spec_source.parent.mkdir(parents=True, exist_ok=True)
        write_request_spec(request_spec, request_spec_source)
    context = kwargs.pop("context", None)
    request = GenerationRequest(
        intent=kwargs.pop("intent"),
        output_directory=kwargs.pop("out_dir"),
        context_name=context.name if context is not None else None,
        options=options,
        approvals=ApprovalSet(
            approve_partial_zeia=options.approve_partial_zeia,
            waive_checksum_recompute=options.waive_checksum_recompute,
            approve_deck_layout=options.approve_deck_layout,
            approve_command_inventory=options.approve_command_inventory,
        ),
        request_spec_path=request_spec_source,
    )
    if kwargs:
        raise AssertionError(f"Unhandled workflow test kwargs: {sorted(kwargs)}")
    return _run_generation_workflow(request)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _private_golden_paths() -> tuple[Path, str] | None:
    """Return (spec_path, context_name) when private goldens are explicitly enabled."""
    if not _env_truthy("TECAN_ENABLE_PRIVATE_GOLDENS"):
        return None
    spec_raw = os.environ.get("TECAN_GOLDEN_SPEC", "").strip()
    context_name = os.environ.get("TECAN_GOLDEN_CONTEXT", "").strip()
    if not spec_raw or not context_name:
        return None
    spec_path = Path(spec_raw).expanduser()
    if not spec_path.is_file():
        return None
    try:
        context = load_project(context_name)
    except Exception:
        return None
    if not (context.root / "manifest.json").exists():
        return None
    if not (context.root / "extracted").exists():
        return None
    return spec_path, context_name


class VerificationV12GoldenCheckHelperTests(unittest.TestCase):
    """Unit-test golden assert helpers against synthetic XSCR fragments only."""

    _GOOD_XSCR = """<?xml version='1.0' encoding='utf-8'?>
<VxData>
  <Name>GripperClose</Name><Values><string>11.0</string></Values>
  <Name>GripperOpen</Name><Values><string>22.0</string></Values>
  <ConfigureDataLabwareDataModel>
    <IsCustomDetailImageUsed>true</IsCustomDetailImageUsed>
  </ConfigureDataLabwareDataModel>
  <SubRoutineStatement>
    <SubRoutine>Lab\\SUB_CapBC_Example</SubRoutine>
    <VariableMapping>
      <Target>InputSubLocation</Target>
      <Source>"Demo_Tube_Pos_1"</Source>
    </VariableMapping>
  </SubRoutineStatement>
  <SubRoutineStatement>
    <SubRoutine>Lab\\SUB_CapBC_Example</SubRoutine>
    <VariableMapping>
      <Target>InputSubLocation</Target>
      <Source>"Demo_Tube_Pos_1"</Source>
    </VariableMapping>
  </SubRoutineStatement>
  <SubRoutineStatement>
    <SubRoutine>Lab\\SUB_CapBC_Example</SubRoutine>
    <VariableMapping>
      <Target>InputSubLocation</Target>
      <Source>"Demo_Tube_Pos_1"</Source>
    </VariableMapping>
  </SubRoutineStatement>
  <SubRoutineStatement>
    <SubRoutine>Lab\\SUB_CapBC_Example</SubRoutine>
    <VariableMapping>
      <Target>InputSubLocation</Target>
      <Source>"Demo_Tube_Pos_1"</Source>
    </VariableMapping>
  </SubRoutineStatement>
  <SubRoutineStatement>
    <SubRoutine>Lab\\SUB_CapBC_Example</SubRoutine>
    <VariableMapping>
      <Target>InputSubLocation</Target>
      <Source>"Demo_Tube_Pos_1"</Source>
    </VariableMapping>
  </SubRoutineStatement>
</VxData>
"""

    def test_helper_accepts_known_good_fragments(self):
        assert_rup_configure_data_labware_booleans_are_lowercase(self._GOOD_XSCR)
        assert_gripper_prep_defaults(self._GOOD_XSCR)
        assert_capbc_input_sub_location_literals(self._GOOD_XSCR)

    def test_helper_rejects_python_style_rup_booleans(self):
        bad = self._GOOD_XSCR.replace(
            "<IsCustomDetailImageUsed>true</IsCustomDetailImageUsed>",
            "<IsCustomDetailImageUsed>True</IsCustomDetailImageUsed>",
        )
        with self.assertRaisesRegex(AssertionError, "Python-style True/False"):
            assert_rup_configure_data_labware_booleans_are_lowercase(bad)

    def test_helper_rejects_zero_gripper_close(self):
        bad = self._GOOD_XSCR.replace("<string>11.0</string>", "<string>0.0</string>", 1)
        with self.assertRaisesRegex(AssertionError, "GripperClose remains zero-like"):
            assert_gripper_prep_defaults(bad)


@unittest.skipUnless(
    _private_golden_paths() is not None,
    "private goldens disabled (set TECAN_ENABLE_PRIVATE_GOLDENS=1, "
    "TECAN_GOLDEN_SPEC, and TECAN_GOLDEN_CONTEXT)",
)
class VerificationV12GoldenRegressionTests(unittest.TestCase):
    def test_regenerated_xscr_retains_critical_fixes(self):
        paths = _private_golden_paths()
        assert paths is not None
        spec_path, context_name = paths
        spec = load_request_spec(spec_path)
        intent = str((spec.get("request") or {}).get("intent") or "private golden regression")
        context = load_project(context_name)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "golden_private"
            # Copy private spec into the temp out-dir so generation does not
            # rewrite a shared path; keep the reviewed content bytes.
            staged_spec = out_dir / "request.spec.yaml"
            manifest = run_generation_workflow(
                intent=intent,
                out_dir=out_dir,
                context=context,
                request_spec=spec,
                request_spec_source=staged_spec,
                simulate=False,
                compile_xscr=True,
            )

            compile_stage = next(
                (stage for stage in manifest.get("stages") or [] if stage.get("id") == "compile_xscr"),
                {},
            )
            self.assertEqual(
                compile_stage.get("status"),
                "passed",
                msg=f"compile_xscr stage failed: {compile_stage.get('summary')}",
            )

            xscr_path = Path(manifest.get("compiled_xscr") or "")
            if not xscr_path.exists():
                candidates = list(out_dir.glob("*.xscr"))
                self.assertTrue(candidates, "compiled XSCR not found in generation output")
                xscr_path = candidates[0]

            xscr_text = xscr_path.read_text(encoding="utf-8")
            ir_path = Path(manifest.get("protocol_ir") or "")
            ir = json.loads(ir_path.read_text(encoding="utf-8")) if ir_path.exists() else None
            gate29 = _gate_subroutine_calls_resolve(
                ir,
                context.manifest,
                xscr_path,
                {"context_root": str(context.root)},
            )
            recipe = spec.get("verification_recipe") if isinstance(spec.get("verification_recipe"), dict) else {}
            prep = recipe.get("capbc_prep") if isinstance(recipe.get("capbc_prep"), dict) else {}
            expected_close = prep.get("GripperClose", recipe.get("tube_gripper_close"))
            expected_open = prep.get("GripperOpen", recipe.get("tube_gripper_open"))
            expected_location = (
                prep.get("TubeLocationName")
                or recipe.get("tube_deck_location")
                or None
            )
            assert_all_verification_v12_golden_xscr_checks(
                xscr_text,
                ir=ir,
                validation_report={"gates": [gate29]},
                expected_close=float(expected_close) if expected_close not in (None, "") else None,
                expected_open=float(expected_open) if expected_open not in (None, "") else None,
                expected_location=str(expected_location).strip() if expected_location else None,
            )


if __name__ == "__main__":
    unittest.main()
