"""Generation selection for source-backed driver command contracts."""

from __future__ import annotations

import unittest

from fluent_pipeline.external_commands import DriverCommandContractAmbiguityError
from fluent_pipeline.generation_workflow import _recipe_step_to_ir


def _next_factory():
    counter = {"n": 0}

    def _next():
        counter["n"] += 1
        return counter["n"], f"step_{counter['n']:03d}"

    return _next


class DriverCommandContractGenerationTests(unittest.TestCase):
    def _contracts(self) -> dict:
        return {
            "schema_version": "tecan.driver_command_contracts.v1",
            "usages": [
                {
                    "contract_id": "dcc_dynamic001",
                    "macro_name": "Vendor_Export",
                    "name": "Vendor_Export",
                    "module_name": "VendorDevice",
                    "command_kind": "ApplicationDriverMacro",
                    "source_script": "ScriptA",
                    "execution_settings": (
                        "&amp;lt;ExportParams&amp;gt;&amp;lt;OutputPath&amp;gt;~OutputDir~\\out.csv"
                        "&amp;lt;/OutputPath&amp;gt;&amp;lt;/ExportParams&amp;gt;"
                    ),
                    "execution_time": "PT2S",
                    "disabled": False,
                    "referenced_variables": ["OutputDir"],
                    "dependency_variables": ["OutputDir"],
                    "variable_declarations": [
                        {
                            "name": "OutputDir",
                            "type": "File",
                            "scope": "Script",
                            "default": "",
                            "assignments": [],
                        }
                    ],
                    "following_companion": None,
                    "source_kind": "script_usage",
                },
                {
                    "contract_id": "dcc_static001",
                    "macro_name": "Vendor_Export",
                    "name": "Vendor_Export",
                    "module_name": "VendorDevice",
                    "command_kind": "ApplicationDriverMacro",
                    "source_script": "ScriptB",
                    "execution_settings": (
                        "&amp;lt;ExportParams&amp;gt;&amp;lt;OutputPath&amp;gt;C:\\Static\\out.csv"
                        "&amp;lt;/OutputPath&amp;gt;&amp;lt;/ExportParams&amp;gt;"
                    ),
                    "execution_time": "PT2S",
                    "disabled": False,
                    "referenced_variables": [],
                    "dependency_variables": [],
                    "variable_declarations": [],
                    "following_companion": None,
                    "source_kind": "script_usage",
                },
            ],
        }

    def test_auto_selects_exact_source_backed_contract(self) -> None:
        built = _recipe_step_to_ir(
            {
                "type": "application_driver_macro",
                "macro_name": "Vendor_Export",
                "module_name": "VendorDevice",
                "contract_id": "dcc_dynamic001",
                "include_variable_defaults": False,
            },
            "Group",
            _next_factory(),
            [],
            driver_contracts=self._contracts(),
        )
        self.assertIsInstance(built, list)
        assert isinstance(built, list)
        run = built[0]
        settings = run["parameters"]["execution_settings"]
        self.assertIn("~OutputDir~", settings)
        self.assertNotIn("C:\\Users", settings)
        self.assertNotIn("C:\\Static", settings)
        self.assertEqual(run["parameters"]["contract_id"], "dcc_dynamic001")
        self.assertIn("~OutputDir~", run["parameters"]["raw_xml"])

    def test_ambiguity_fails_deterministically(self) -> None:
        with self.assertRaises(DriverCommandContractAmbiguityError) as raised:
            _recipe_step_to_ir(
                {
                    "type": "application_driver_macro",
                    "macro_name": "Vendor_Export",
                    "module_name": "VendorDevice",
                },
                "Group",
                _next_factory(),
                [],
                driver_contracts=self._contracts(),
            )
        self.assertEqual(raised.exception.contract_ids, ["dcc_dynamic001", "dcc_static001"])

    def test_a200_dispense_autofills_settings_from_contract(self) -> None:
        contracts = {
            "usages": [
                {
                    "contract_id": "dcc_a200",
                    "macro_name": "DriverRun",
                    "name": "DriverRun",
                    "module_name": "DriverModule",
                    "command_kind": "LegacyDriverMacro",
                    "execution_settings": "CUSTOM,~a200startwell~,~a200endwell~,0",
                    "execution_time": "PT2S",
                    "disabled": False,
                    "following_companion": {
                        "name": "DriverWait",
                        "module_name": "DriverModule",
                        "execution_settings": "120",
                        "execution_time": "PT2S",
                        "disabled": False,
                    },
                    "variable_declarations": [],
                }
            ]
        }
        built = _recipe_step_to_ir(
            {
                "type": "a200_dispense",
                "volume_ul": 50,
                "macro_name": "DriverRun",
                "wait_macro": "DriverWait",
                "module_name": "DriverModule",
                "start_well": 1,
                "end_well": 8,
            },
            "Group",
            _next_factory(),
            [],
            driver_contracts=contracts,
        )
        self.assertIsInstance(built, list)
        assert isinstance(built, list)
        run = next(step for step in built if step["name"].endswith(" Run"))
        self.assertEqual(
            run["parameters"]["execution_settings"],
            "CUSTOM,~a200startwell~,~a200endwell~,0",
        )
        wait = next(step for step in built if step["name"].endswith(" Wait Finished"))
        self.assertEqual(wait["parameters"]["execution_settings"], "120")


if __name__ == "__main__":
    unittest.main()
