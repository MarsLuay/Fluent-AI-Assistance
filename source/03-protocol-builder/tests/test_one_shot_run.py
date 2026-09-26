from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.application_services import (
    FullExportReadinessResult,
    GenerationResult,
    OneShotRunRequest,
    run_one_shot,
)
from fluent_pipeline.cli.parser import _build_parser


def _context(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        manifest={
            "canonical_model": {
                "detection": {
                    "software_family": {
                        "software_family": "fluentcontrol",
                        "status": "verified",
                    }
                }
            }
        },
    )


def _ready() -> FullExportReadinessResult:
    return FullExportReadinessResult(
        request=mock.sentinel.request,
        readiness={"accepted": True, "status": "complete", "summary": "ready"},
    )


class OneShotRunTests(unittest.TestCase):
    def test_cli_parser_requires_exactly_one_request_source(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["run", "--input", "export.zeia", "--request", "make it"])
        self.assertEqual(args.cmd, "run")
        self.assertEqual(args.func.__name__, "_cmd_run")
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--input", "export.zeia"])

    def test_single_archive_uses_shared_services_and_deterministic_output_path(self) -> None:
        archive = Path("export.zeia")
        generation = GenerationResult(
            request=mock.sentinel.generation_request,
            manifest={"ready_to_import": True},
        )
        with mock.patch(
            "fluent_pipeline.application_services.discover_zeia_paths",
            return_value=[archive],
        ), mock.patch(
            "fluent_pipeline.application_services.import_project_context",
            return_value=_context("demo"),
        ) as import_project, mock.patch(
            "fluent_pipeline.application_services.resolve_full_export_readiness",
            return_value=_ready(),
        ), mock.patch(
            "fluent_pipeline.application_services.create_request_spec",
            side_effect=lambda request: SimpleNamespace(spec={}, output_path=request.output_path),
        ) as create_spec, mock.patch(
            "fluent_pipeline.application_services.validate_request_spec",
            return_value=SimpleNamespace(result=SimpleNamespace(ok=True)),
        ), mock.patch(
            "fluent_pipeline.request_factory.build_generation_request_from_spec",
            return_value=mock.sentinel.generation_request,
        ), mock.patch(
            "fluent_pipeline.application_services.generate_protocol",
            return_value=generation,
        ) as generate:
            first = run_one_shot(OneShotRunRequest(input_path=archive, request="make it"))
            second = run_one_shot(OneShotRunRequest(input_path=archive, request="make it"))

        self.assertTrue(first.ok)
        self.assertEqual(first.exit_code, 0)
        self.assertEqual(first.request_spec_path, second.request_spec_path)
        import_project.assert_has_calls(
            [
                mock.call(archive, name="export", force=False, snapshot_archives=[]),
                mock.call(archive, name="export", force=False, snapshot_archives=[]),
            ]
        )
        self.assertEqual(create_spec.call_count, 2)
        self.assertEqual(generate.call_count, 2)

    def test_request_file_is_supported_and_inline_request_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request_file = Path(tmp) / "request.txt"
            request_file.write_text("make it from a file", encoding="utf-8")
            archive = Path(tmp) / "export.zeia"
            generation = GenerationResult(
                request=mock.sentinel.generation_request,
                manifest={"ready_to_import": True},
            )
            with mock.patch(
                "fluent_pipeline.application_services.discover_zeia_paths",
                return_value=[archive],
            ), mock.patch(
                "fluent_pipeline.application_services.import_project_context",
                return_value=_context("demo"),
            ), mock.patch(
                "fluent_pipeline.application_services.resolve_full_export_readiness",
                return_value=_ready(),
            ), mock.patch(
                "fluent_pipeline.application_services.create_request_spec",
                side_effect=lambda request: SimpleNamespace(spec={}, output_path=request.output_path),
            ), mock.patch(
                "fluent_pipeline.application_services.validate_request_spec",
                return_value=SimpleNamespace(result=SimpleNamespace(ok=True)),
            ), mock.patch(
                "fluent_pipeline.request_factory.build_generation_request_from_spec",
                return_value=mock.sentinel.generation_request,
            ), mock.patch(
                "fluent_pipeline.application_services.generate_protocol",
                return_value=generation,
            ):
                result = run_one_shot(
                    OneShotRunRequest(input_path=archive, request_file=request_file)
                )
                rejected = run_one_shot(
                    OneShotRunRequest(
                        input_path=archive,
                        request="inline",
                        request_file=request_file,
                    )
                )

        self.assertTrue(result.ok)
        self.assertEqual(rejected.failed_stage, "request")
        self.assertEqual(rejected.exit_code, 3)

    def test_collection_and_partial_approval_reach_readiness_before_generation(self) -> None:
        archives = (Path("one.zeia"), Path("two.zeia"))
        with mock.patch(
            "fluent_pipeline.application_services.discover_zeia_paths",
            return_value=list(archives),
        ), mock.patch(
            "fluent_pipeline.application_services.import_project_context",
            side_effect=[_context("one"), _context("two")],
        ), mock.patch(
            "fluent_pipeline.application_services.create_project_collection",
            return_value=_context("collection"),
        ) as create_collection, mock.patch(
            "fluent_pipeline.application_services.resolve_full_export_readiness",
            return_value=_ready(),
        ) as resolve_readiness, mock.patch(
            "fluent_pipeline.application_services.create_request_spec",
            side_effect=lambda request: SimpleNamespace(spec={}, output_path=request.output_path),
        ), mock.patch(
            "fluent_pipeline.application_services.validate_request_spec",
            return_value=SimpleNamespace(result=SimpleNamespace(ok=True)),
        ), mock.patch(
            "fluent_pipeline.request_factory.build_generation_request_from_spec",
            return_value=mock.sentinel.generation_request,
        ), mock.patch(
            "fluent_pipeline.application_services.generate_protocol",
            return_value=GenerationResult(mock.sentinel.generation_request, {"ready_to_import": True}),
        ):
            result = run_one_shot(
                OneShotRunRequest(
                    input_path=Path("exports"),
                    request="make it",
                    approve_partial_zeia=True,
                )
            )

        self.assertTrue(result.ok)
        create_collection.assert_called_once_with("one", ["one", "two"], force=False)
        readiness_request = resolve_readiness.call_args.args[0]
        self.assertEqual(readiness_request.context_name, "collection")
        self.assertTrue(readiness_request.approve_partial_zeia)

    def test_readiness_failure_prevents_request_spec_and_generation(self) -> None:
        archive = Path("partial.zeia")
        with mock.patch(
            "fluent_pipeline.application_services.discover_zeia_paths",
            return_value=[archive],
        ), mock.patch(
            "fluent_pipeline.application_services.import_project_context",
            return_value=_context("partial"),
        ), mock.patch(
            "fluent_pipeline.application_services.resolve_full_export_readiness",
            return_value=FullExportReadinessResult(
                request=mock.sentinel.request,
                readiness={"accepted": False, "summary": "missing dependency"},
            ),
        ), mock.patch(
            "fluent_pipeline.application_services.create_request_spec"
        ) as create_spec, mock.patch(
            "fluent_pipeline.application_services.generate_protocol"
        ) as generate:
            result = run_one_shot(OneShotRunRequest(input_path=archive, request="make it"))

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_stage, "readiness")
        self.assertEqual(result.exit_code, 5)
        create_spec.assert_not_called()
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
