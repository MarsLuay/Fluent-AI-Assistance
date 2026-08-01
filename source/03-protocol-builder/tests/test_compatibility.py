import unittest

from fluent_pipeline.compatibility import (
    TargetSetup,
    build_compatibility_report,
    render_compatibility_markdown,
)


class CompatibilityMatrixTests(unittest.TestCase):
    def test_report_extracts_current_manual_metadata(self):
        report = build_compatibility_report(connector="unitelabs")

        self.assertEqual(report["schema_version"], "tecan.fluent_compatibility_matrix.v2")
        self.assertEqual(report["manual"]["fluentcontrol_version"], "3.8 SP1")
        self.assertEqual(report["manual"]["document_version"], "399935_en_V2_9")
        self.assertIn("Windows 11", report["manual"]["windows_environment"])
        self.assertTrue(report["manual"]["evidence"])

    def test_report_extracts_connector_package_metadata(self):
        report = build_compatibility_report(connector="unitelabs")
        connector = report["connectors"][0]

        self.assertEqual(connector["package_name"], "unitelabs-tecan-fluentcontrol")
        self.assertEqual(connector["package_version"], "0.3.0")
        self.assertEqual(connector["python_requirement"], ">=3.10,<4.0")
        self.assertIn("comtypes==1.4.6", connector["dependencies"])
        self.assertIn("SiLA 2 1.1 feature XML v1.0", connector["feature_versions"])

    def test_default_matrix_marks_unitelabs_current_manual_untested(self):
        report = build_compatibility_report(connector="unitelabs")
        current_rows = [
            row
            for row in report["rows"]
            if row["connector_key"] == "unitelabs"
            and row["fluentcontrol_version"] == "3.8 SP1"
        ]

        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0]["status"], "untested_current_manual")
        self.assertEqual(current_rows[0]["confidence"], "medium")
        self.assertIn("3.4.9.61784", current_rows[0]["connector_tested"])
        self.assertIn("3.4.10.62215", current_rows[0]["connector_tested"])

    def test_default_matrix_includes_unitelabs_registry_tested_builds(self):
        report = build_compatibility_report(connector="unitelabs")
        tested_builds = {
            row["fluentcontrol_build"]
            for row in report["rows"]
            if row["status"] == "tested_by_public_registry"
        }

        self.assertEqual(tested_builds, {"3.4.9.61784", "3.4.10.62215"})

    def test_rows_attach_structured_evidence_with_confidence_and_links(self):
        report = build_compatibility_report(connector="unitelabs")
        row = report["rows"][0]

        self.assertTrue(row["evidence"])
        self.assertTrue(all("confidence" in item for item in row["evidence"]))
        self.assertTrue(any(item.get("path") for item in row["evidence"]))
        self.assertTrue(any(item.get("url") for item in row["evidence"]))

    def test_specific_tested_build_is_classified_from_registry_rules(self):
        report = build_compatibility_report(
            connector="unitelabs",
            target=TargetSetup(
                fluentcontrol_version="3.4 SP1",
                fluentcontrol_build="3.4.10.62215",
                manual_version="FluentControl 3.4 SP1",
                windows_environment="Windows 10 Enterprise LTSC 2021",
            ),
        )

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["status"], "tested_by_public_registry")
        self.assertEqual(report["rows"][0]["confidence"], "high")

    def test_markdown_includes_required_matrix_columns(self):
        report = build_compatibility_report(connector="unitelabs")
        markdown = render_compatibility_markdown(report)

        self.assertIn("| FluentControl | Build | Manual | Connector | Connector version |", markdown)
        self.assertIn("SiLA/API compatibility", markdown)
        self.assertIn("Windows environment", markdown)
        self.assertIn("Tested?", markdown)
        self.assertIn("Confidence", markdown)
        self.assertIn("## Extracted Metadata", markdown)


if __name__ == "__main__":
    unittest.main()
