import copy
import unittest

import fluent_pipeline.protocol_ir_schema as schema_module
from fluent_pipeline.protocol_ir import (
    CANONICAL_IR_BUNDLE_VERSION,
    CANONICAL_IR_VERSION,
    migrate_protocol_ir,
    operation_specs,
    protocol_ir_bundle_json_schema,
    protocol_ir_json_schema,
    protocol_ir_migration_path,
    protocol_ir_schema_markdown,
    register_protocol_ir_migration,
    validate_protocol_ir_document,
)


class ProtocolIRSchemaTests(unittest.TestCase):
    def test_schema_exports_operation_contract_and_docs(self):
        schema = protocol_ir_json_schema()
        bundle_schema = protocol_ir_bundle_json_schema()
        markdown = protocol_ir_schema_markdown()

        self.assertEqual(schema["properties"]["ir_version"]["const"], CANONICAL_IR_VERSION)
        self.assertEqual(CANONICAL_IR_VERSION, "tecan.protocol_ir.v2")
        self.assertIn("steps", schema["required"])
        self.assertIn("expression", schema["$defs"])
        self.assertEqual(
            schema["$defs"]["variable"]["properties"]["default_expression"]["$ref"],
            "#/$defs/expression",
        )
        self.assertIn("aspirate", schema["$defs"]["step"]["properties"]["operation"]["enum"])
        self.assertIn("set_variable", schema["$defs"]["step"]["properties"]["operation"]["enum"])
        self.assertIn("query_variable", schema["$defs"]["step"]["properties"]["operation"]["enum"])
        self.assertIn("execute_application", schema["$defs"]["step"]["properties"]["operation"]["enum"])
        self.assertEqual(schema["properties"]["variables"]["items"]["$ref"], "#/$defs/variable")
        self.assertEqual(bundle_schema["properties"]["ir_version"]["const"], CANONICAL_IR_BUNDLE_VERSION)
        self.assertEqual(CANONICAL_IR_BUNDLE_VERSION, "tecan.protocol_ir.bundle.v2")
        self.assertIn("protocols", bundle_schema["required"])
        self.assertIn("## Operation Enum", markdown)
        self.assertIn("`aspirate`", markdown)
        self.assertIn("`set_remaining_runtime`", markdown)

        specs = {spec.operation: spec for spec in operation_specs()}
        self.assertTrue(specs["aspirate"].requires_volume_ul)
        self.assertTrue(specs["aspirate"].requires_liquid_class)
        self.assertTrue(specs["pick_up_tips"].requires_labware_target)
        self.assertFalse(specs["comment"].requires_labware_target)

        versions = {info.version: info for info in schema_module.protocol_ir_schema_versions()}
        self.assertIn("tecan.protocol_ir.v1", versions)
        self.assertIn("tecan.protocol_ir.v2", versions)
        self.assertFalse(versions["tecan.protocol_ir.v1"].current)
        self.assertTrue(versions["tecan.protocol_ir.v2"].current)

    def test_validate_document_rejects_missing_fields_and_invalid_operations(self):
        missing = {
            "ir_version": CANONICAL_IR_VERSION,
            "id": "incomplete",
            "protocol": {"name": "Incomplete"},
        }
        missing_paths = {issue.path for issue in validate_protocol_ir_document(missing)}
        self.assertIn("$.source", missing_paths)
        self.assertIn("$.steps", missing_paths)

        invalid = _minimal_ir()
        invalid["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "aspirate",
                "name": "Aspirate",
                "target_labware": "SourcePlate",
                "parameters": {},
            }
        ]
        issues = validate_protocol_ir_document(invalid)
        issue_paths = {issue.path for issue in issues}
        self.assertIn("$.steps[0].volume_ul", issue_paths)
        self.assertIn("$.steps[0].liquid_class", issue_paths)

        bad_operation = _minimal_ir()
        bad_operation["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "teleport_liquid",
                "name": "Teleport",
                "parameters": {},
            }
        ]
        self.assertIn("$.steps[0].operation", {issue.path for issue in validate_protocol_ir_document(bad_operation)})

    def test_add_labware_fc_variable_name_validation(self):
        invalid = _minimal_ir()
        invalid["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Setup",
                "operation": "add_labware",
                "name": "Add Labware",
                "parameters": {"labware_type": "@fc:1BadName", "label": "Source"},
            }
        ]
        issue_paths = {issue.path for issue in validate_protocol_ir_document(invalid)}
        self.assertIn("$.steps[0].parameters.labware_type", issue_paths)

    def test_migration_registry_can_migrate_v1_to_future_versions(self):
        saved_migrations = dict(schema_module.PROTOCOL_IR_MIGRATIONS)
        saved_schemas = copy.deepcopy(schema_module.PROTOCOL_IR_SCHEMAS)
        saved_descriptions = dict(schema_module.PROTOCOL_IR_VERSION_DESCRIPTIONS)
        future_version = "tecan.protocol_ir.v2.test"
        try:
            future_schema = protocol_ir_json_schema()
            future_schema["$id"] = "https://fluent-ai-assistance.local/schemas/protocol_ir.v2.test.schema.json"
            future_schema["properties"]["ir_version"] = {"const": future_version}

            def migrate_to_future(payload):
                migrated = copy.deepcopy(payload)
                migrated["ir_version"] = future_version
                migrated["schema_notes"] = {"migrated_from": payload["ir_version"]}
                return migrated

            register_protocol_ir_migration(
                CANONICAL_IR_VERSION,
                future_version,
                migrate_to_future,
                schema=future_schema,
                description="Test future version.",
            )

            self.assertEqual(protocol_ir_migration_path(CANONICAL_IR_VERSION, future_version), [CANONICAL_IR_VERSION, future_version])
            migrated = migrate_protocol_ir(_minimal_ir(), to_version=future_version)
            self.assertEqual(migrated["ir_version"], future_version)
            self.assertEqual(migrated["schema_notes"]["migrated_from"], CANONICAL_IR_VERSION)
            self.assertEqual(protocol_ir_json_schema(future_version)["properties"]["ir_version"]["const"], future_version)
        finally:
            schema_module.PROTOCOL_IR_MIGRATIONS.clear()
            schema_module.PROTOCOL_IR_MIGRATIONS.update(saved_migrations)
            schema_module.PROTOCOL_IR_SCHEMAS.clear()
            schema_module.PROTOCOL_IR_SCHEMAS.update(saved_schemas)
            schema_module.PROTOCOL_IR_VERSION_DESCRIPTIONS.clear()
            schema_module.PROTOCOL_IR_VERSION_DESCRIPTIONS.update(saved_descriptions)

    def test_v1_migrates_to_v2_typed_expression_contract(self):
        legacy = _minimal_ir()
        legacy["ir_version"] = "tecan.protocol_ir.v1"
        legacy["variables"] = [
            {
                "name": "TubeType",
                "type": "String",
                "default_value": "50ml Falcon",
            },
            {
                "name": "TubeCount",
                "type": "Integer",
                "default_value": 1,
            },
        ]
        legacy["labware"] = [
            {
                "label": "Rack",
                "catalog": "Tube Rack",
                "location": "Nest",
                "position": 'GetCoverSiteIndex("RackPark")',
            }
        ]
        legacy["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Setup",
                "operation": "set_variable",
                "name": "Set tube type",
                "parameters": {
                    "variable": "TubeType",
                    "value": '"50ml Falcon"',
                },
            },
            {
                "id": "step_002",
                "index": 2,
                "group": "Setup",
                "operation": "conditional_branch",
                "name": "Check count",
                "parameters": {
                    "condition": "TubeCount > 0",
                },
            },
        ]

        migrated = migrate_protocol_ir(legacy)

        self.assertEqual(migrated["ir_version"], "tecan.protocol_ir.v2")
        self.assertEqual(migrated["expression_schema_version"], "fluent_control.expression.v1")
        self.assertEqual(migrated["variables"][0]["default_expression"]["kind"], "string_literal")
        self.assertEqual(migrated["variables"][0]["default_expression"]["value"], "50ml Falcon")
        self.assertEqual(migrated["labware"][0]["position_expression"]["kind"], "function_call")
        self.assertEqual(migrated["steps"][0]["parameters"]["value_expression"]["kind"], "string_literal")
        self.assertEqual(migrated["steps"][1]["parameters"]["condition_expression"]["kind"], "binary_expression")
        self.assertEqual(validate_protocol_ir_document(migrated), [])

    def test_validate_document_rejects_semantically_invalid_expressions(self):
        invalid = _minimal_ir()
        invalid["variables"] = [
            {
                "name": "TubeCount",
                "type": "Integer",
                "default_expression": {"kind": "string_literal", "value": "not a number"},
            }
        ]
        invalid["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Setup",
                "operation": "conditional_branch",
                "name": "Bad condition",
                "parameters": {
                    "condition_expression": {
                        "kind": "binary_expression",
                        "operator": "AND",
                        "left": {"kind": "variable_reference", "name": "MissingVariable"},
                        "right": {"kind": "function_call", "name": "UnknownFunction", "arguments": []},
                    }
                },
            }
        ]

        issues = validate_protocol_ir_document(invalid)
        messages = [issue.message for issue in issues]

        self.assertTrue(any("assignment_type_mismatch" in message for message in messages))
        self.assertTrue(any("undefined_variable" in message for message in messages))
        self.assertTrue(any("unknown_function" in message for message in messages))

    def test_validate_document_discovers_nested_plural_expression_lists(self):
        invalid = _minimal_ir()
        invalid["variables"] = [{"name": "RuntimeChoice", "type": "Integer"}]
        invalid["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Setup",
                "operation": "runtime_variable_prompt",
                "name": "Runtime prompt",
                "parameters": {
                    "variables": [
                        {
                            "name": "RuntimeChoice",
                            "value_expressions": [
                                {"kind": "variable_reference", "name": "MissingVariable"}
                            ],
                        }
                    ]
                },
            }
        ]

        issues = validate_protocol_ir_document(invalid)

        self.assertTrue(
            any(
                issue.path == "$.steps[0].parameters.variables[0].value_expressions[0]"
                and "undefined_variable" in issue.message
                for issue in issues
            )
        )

    def test_v2_migration_coerces_typed_numeric_variable_defaults(self):
        legacy = _minimal_ir()
        legacy["ir_version"] = "tecan.protocol_ir.v1"
        legacy["variables"] = [
            {
                "name": "GripperClose",
                "type": "Floating Point",
                "default_value": "11",
            }
        ]

        migrated = migrate_protocol_ir(legacy)

        self.assertEqual(migrated["variables"][0]["default_expression"], {"kind": "number_literal", "value": 11})
        self.assertEqual(validate_protocol_ir_document(migrated), [])

    def test_v2_migration_preserves_declared_string_default_even_when_numeric(self):
        legacy = _minimal_ir()
        legacy["ir_version"] = "tecan.protocol_ir.v1"
        legacy["variables"] = [
            {
                "name": "TubeLocationName",
                "type": "String",
                "default_value": 0,
            }
        ]

        migrated = migrate_protocol_ir(legacy)

        self.assertEqual(migrated["variables"][0]["default_expression"], {"kind": "string_literal", "value": "0"})
        self.assertEqual(validate_protocol_ir_document(migrated), [])

    def test_v2_preserves_fluent_string_choice_list_as_one_string_literal(self):
        legacy = _minimal_ir()
        legacy["ir_version"] = "tecan.protocol_ir.v1"
        legacy["variables"] = [
            {
                "name": "CapsOnTube",
                "type": "String",
                "default_value": '"yes","no"',
                "values": ['"yes","no"'],
            }
        ]

        migrated = migrate_protocol_ir(legacy)

        expected = {"kind": "string_literal", "value": '"yes","no"'}
        self.assertEqual(migrated["variables"][0]["default_expression"], expected)
        self.assertEqual(migrated["variables"][0]["value_expressions"], [expected])
        self.assertEqual(validate_protocol_ir_document(migrated), [])

    def test_v2_migration_keeps_undeclared_bare_string_assignment_literal(self):
        legacy = _minimal_ir()
        legacy["ir_version"] = "tecan.protocol_ir.v1"
        legacy["variables"] = [
            {
                "name": "TubeLocationName",
                "type": "String",
                "default_value": "",
            },
            {
                "name": "LASTBARCODE",
                "type": "String",
                "default_value": "",
            },
        ]
        legacy["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Setup",
                "operation": "set_variable",
                "name": "Set location",
                "parameters": {"variable": "TubeLocationName", "value": "Demo_Tube_Pos_1"},
            },
            {
                "id": "step_002",
                "index": 2,
                "group": "Setup",
                "operation": "set_variable",
                "name": "Copy barcode",
                "parameters": {"variable": "TubeLocationName", "value": "LASTBARCODE"},
            },
        ]

        migrated = migrate_protocol_ir(legacy)

        self.assertEqual(
            migrated["steps"][0]["parameters"]["value_expression"],
            {"kind": "string_literal", "value": "Demo_Tube_Pos_1"},
        )
        self.assertEqual(
            migrated["steps"][1]["parameters"]["value_expression"],
            {"kind": "variable_reference", "name": "LASTBARCODE"},
        )
        self.assertEqual(validate_protocol_ir_document(migrated), [])

    def test_v2_allows_expression_only_volume(self):
        ir = _minimal_ir()
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "aspirate",
                "name": "Aspirate",
                "target_labware": "SourcePlate",
                "volume_ul_expression": {"kind": "number_literal", "value": 100},
                "liquid_class": "Water",
                "parameters": {},
            }
        ]

        self.assertEqual(validate_protocol_ir_document(ir), [])
        normalized = migrate_protocol_ir(ir)
        self.assertNotIn("volume_ul", normalized["steps"][0])
        self.assertEqual(
            normalized["steps"][0]["volume_ul_expression"],
            {"kind": "number_literal", "value": 100},
        )

    def test_v2_rejects_undefined_variable_when_declaration_table_is_empty(self):
        ir = _minimal_ir()
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "aspirate",
                "name": "Aspirate",
                "target_labware": "SourcePlate",
                "volume_ul_expression": {
                    "kind": "variable_reference",
                    "name": "TransferVolume",
                },
                "liquid_class": "Water",
                "parameters": {},
            }
        ]

        issues = validate_protocol_ir_document(ir)

        self.assertTrue(
            any(
                issue.path == "$.steps[0].volume_ul_expression"
                and "undefined_variable" in issue.message
                and "TransferVolume" in issue.message
                for issue in issues
            )
        )

    def test_v2_allows_undeclared_variable_in_disabled_condition(self):
        ir = _minimal_ir()
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Control",
                "operation": "conditional_branch",
                "name": "Disabled condition",
                "parameters": {
                    "is_disabled_for_execution": True,
                    "condition_expression": {
                        "kind": "binary_expression",
                        "operator": "=",
                        "left": {"kind": "variable_reference", "name": "ErrorEndNow"},
                        "right": {"kind": "number_literal", "value": 1},
                    },
                },
            }
        ]

        self.assertEqual(validate_protocol_ir_document(ir), [])

    def test_v1_export_derives_scalars_only_at_compatibility_boundary(self):
        ir = _minimal_ir()
        ir["variables"] = [
            {
                "name": "TubeCount",
                "type": "Integer",
                "default_expression": {"kind": "number_literal", "value": 4},
            }
        ]
        ir["labware"] = [
            {
                "label": "SourcePlate",
                "position_expression": {
                    "kind": "function_call",
                    "name": "GetCoverSiteIndex",
                    "arguments": [{"kind": "string_literal", "value": "Park"}],
                },
            }
        ]
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "aspirate",
                "name": "Aspirate",
                "target_labware": "SourcePlate",
                "volume_ul_expression": {"kind": "number_literal", "value": 100},
                "liquid_class": "Water",
                "parameters": {},
            },
            {
                "id": "step_002",
                "index": 2,
                "group": "Control",
                "operation": "conditional_branch",
                "name": "Condition",
                "parameters": {
                    "condition_expression": {
                        "kind": "binary_expression",
                        "operator": ">",
                        "left": {"kind": "variable_reference", "name": "TubeCount"},
                        "right": {"kind": "number_literal", "value": 0},
                    }
                },
            },
            {
                "id": "step_003",
                "index": 3,
                "group": "Control",
                "operation": "loop_over_wells",
                "name": "Loop",
                "parameters": {
                    "number_of_loops_expression": {
                        "kind": "variable_reference",
                        "name": "TubeCount",
                    }
                },
            },
            {
                "id": "step_004",
                "index": 4,
                "group": "Move",
                "operation": "move_plate",
                "name": "Move",
                "target_labware": "SourcePlate",
                "parameters": {
                    "site_expression": {
                        "kind": "function_call",
                        "name": "GetCoverSiteIndex",
                        "arguments": [{"kind": "string_literal", "value": "Park"}],
                    }
                },
            },
        ]

        exported = migrate_protocol_ir(ir, to_version="tecan.protocol_ir.v1")

        self.assertEqual(exported["ir_version"], "tecan.protocol_ir.v1")
        self.assertNotIn("expression_schema_version", exported)
        self.assertEqual(exported["variables"][0]["default_value"], 4)
        self.assertEqual(exported["labware"][0]["position"], 'GetCoverSiteIndex("Park")')
        self.assertEqual(exported["steps"][0]["volume_ul"], 100)
        self.assertEqual(exported["steps"][1]["parameters"]["condition"], "(TubeCount > 0)")
        self.assertEqual(exported["steps"][2]["parameters"]["number_of_loops"], "TubeCount")
        self.assertEqual(exported["steps"][3]["parameters"]["site"], 'GetCoverSiteIndex("Park")')
        self.assertFalse(_contains_expression_field(exported))

    def test_v1_export_canonicalizes_site_expression_aliases(self):
        site_expression = {
            "kind": "function_call",
            "name": "GetCoverSiteIndex",
            "arguments": [{"kind": "string_literal", "value": "Park"}],
        }

        for expression_key in (
            "site_expression",
            "destination_site_expression",
            "to_site_expression",
        ):
            with self.subTest(expression_key=expression_key):
                ir = _minimal_ir()
                ir["steps"] = [
                    {
                        "id": "step_001",
                        "index": 1,
                        "group": "Move",
                        "operation": "move_plate",
                        "name": "Move",
                        "target_labware": "SourcePlate",
                        "parameters": {expression_key: copy.deepcopy(site_expression)},
                    }
                ]

                normalized = migrate_protocol_ir(ir)
                normalized_params = normalized["steps"][0]["parameters"]
                self.assertEqual(normalized_params["site_expression"], site_expression)
                self.assertNotIn("destination_site_expression", normalized_params)
                self.assertNotIn("to_site_expression", normalized_params)

                exported = migrate_protocol_ir(ir, to_version="tecan.protocol_ir.v1")

                self.assertEqual(exported["steps"][0]["parameters"]["site"], 'GetCoverSiteIndex("Park")')
                self.assertFalse(_contains_expression_field(exported))

    def test_v2_rejects_conflicting_site_expression_aliases(self):
        ir = _minimal_ir()
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Move",
                "operation": "move_plate",
                "name": "Move",
                "target_labware": "SourcePlate",
                "parameters": {
                    "site_expression": {"kind": "number_literal", "value": 1},
                    "destination_site_expression": {"kind": "number_literal", "value": 2},
                },
            }
        ]

        issues = validate_protocol_ir_document(ir)

        self.assertTrue(
            any(
                issue.path == "$.steps[0].parameters.destination_site_expression"
                and "disagrees with site_expression" in issue.message
                for issue in issues
            )
        )
        with self.assertRaises(schema_module.ProtocolIRValidationError):
            migrate_protocol_ir(ir, validate=False)

    def test_v1_export_rejects_disagreeing_v2_projection_even_without_validation(self):
        ir = _minimal_ir()
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "aspirate",
                "name": "Aspirate",
                "target_labware": "SourcePlate",
                "volume_ul": 100,
                "volume_ul_expression": {"kind": "number_literal", "value": 200},
                "liquid_class": "Water",
                "parameters": {},
            }
        ]

        with self.assertRaises(schema_module.ProtocolIRValidationError):
            migrate_protocol_ir(
                ir,
                to_version="tecan.protocol_ir.v1",
                validate=False,
            )

    def test_v2_rejects_disagreeing_scalar_and_expression_projections(self):
        ir = _minimal_ir()
        ir["variables"] = [
            {
                "name": "TubeCount",
                "type": "Integer",
                "default_value": 4,
                "default_expression": {"kind": "number_literal", "value": 5},
            }
        ]
        ir["labware"] = [
            {
                "label": "SourcePlate",
                "position": 1,
                "position_expression": {"kind": "number_literal", "value": 2},
            }
        ]
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Transfer",
                "operation": "aspirate",
                "name": "Aspirate",
                "target_labware": "SourcePlate",
                "volume_ul": 100,
                "volume_ul_expression": {"kind": "number_literal", "value": 200},
                "liquid_class": "Water",
                "parameters": {},
            },
            {
                "id": "step_002",
                "index": 2,
                "group": "Control",
                "operation": "conditional_branch",
                "name": "Condition",
                "parameters": {
                    "condition": "TubeCount > 0",
                    "condition_expression": {
                        "kind": "binary_expression",
                        "operator": ">",
                        "left": {"kind": "variable_reference", "name": "TubeCount"},
                        "right": {"kind": "number_literal", "value": 1},
                    },
                },
            },
        ]

        issue_paths = {issue.path for issue in validate_protocol_ir_document(ir)}

        self.assertIn("$.variables[0].default_expression", issue_paths)
        self.assertIn("$.labware[0].position_expression", issue_paths)
        self.assertIn("$.steps[0].volume_ul_expression", issue_paths)
        self.assertIn("$.steps[1].parameters.condition_expression", issue_paths)

    def test_v2_accepts_integer_arithmetic_loop_count_expression(self):
        ir = _minimal_ir()
        ir["variables"] = [{"name": "TubeCount", "type": "Integer"}]
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Control",
                "operation": "loop_over_wells",
                "name": "Repeat",
                "parameters": {
                    "number_of_loops_expression": {
                        "kind": "binary_expression",
                        "operator": "+",
                        "left": {"kind": "variable_reference", "name": "TubeCount"},
                        "right": {"kind": "number_literal", "value": 1},
                    }
                },
            }
        ]

        issues = validate_protocol_ir_document(ir)

        self.assertFalse(
            any(
                issue.path == "$.steps[0].parameters.number_of_loops_expression"
                for issue in issues
            )
        )

    def test_source_preserved_expression_requires_ingestion_reference_metadata(self):
        ir = _minimal_ir()
        ir["variables"] = [
            {"name": "Result", "scope": "Script", "type": "String", "value": ""}
        ]
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Test",
                "operation": "set_variable",
                "name": "Set Result",
                "parameters": {
                    "variable": "Result",
                    "value_expression": {
                        "kind": "source_preserved_expression",
                        "source": "Unsupported.Syntax(A)",
                        "source_hash": "sha256:" + ("1" * 64),
                        "source_entry": "source.zeia!DataStore/main.xscr",
                        "byte_stable": True,
                    },
                },
            }
        ]

        issue_paths = {issue.path for issue in validate_protocol_ir_document(ir)}

        expression_path = "$.steps[0].parameters.value_expression"
        self.assertIn(f"{expression_path}.reference_metadata_origin", issue_paths)
        self.assertIn(f"{expression_path}.referenced_variables", issue_paths)
        self.assertIn(f"{expression_path}.referenced_functions", issue_paths)

    def test_reviewed_raw_expression_requires_reviewer_approved_reference_metadata(self):
        ir = _minimal_ir()
        ir["variables"] = [
            {"name": "Result", "scope": "Script", "type": "String", "value": ""}
        ]
        ir["steps"] = [
            {
                "id": "step_001",
                "index": 1,
                "group": "Test",
                "operation": "set_variable",
                "name": "Set Result",
                "parameters": {
                    "variable": "Result",
                    "value_expression": {
                        "kind": "reviewed_raw_expression",
                        "source": "Reviewed.Syntax(A)",
                        "approval_id": "approval-123",
                    },
                },
            }
        ]

        issue_paths = {issue.path for issue in validate_protocol_ir_document(ir)}

        expression_path = "$.steps[0].parameters.value_expression"
        self.assertIn(f"{expression_path}.reviewer", issue_paths)
        self.assertIn(f"{expression_path}.reference_metadata_origin", issue_paths)
        self.assertIn(f"{expression_path}.referenced_variables", issue_paths)
        self.assertIn(f"{expression_path}.referenced_functions", issue_paths)


def _minimal_ir():
    return {
        "ir_version": CANONICAL_IR_VERSION,
        "id": "minimal",
        "protocol": {"name": "Minimal protocol", "comment": ""},
        "source": {"format": "test", "path": ""},
        "worktable": {"name": "780_Empty", "guid": "", "auto_place": False},
        "labware": [],
        "reagents": [],
        "liquid_classes": [],
        "variables": [],
        "worklists": [],
        "dependencies": [],
        "safety_assumptions": [{"id": "manual_validation_required", "text": "Review before use."}],
        "steps": [],
    }


def _contains_expression_field(value):
    if isinstance(value, dict):
        return any(
            key.endswith("_expression")
            or key.endswith("_expressions")
            or _contains_expression_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_expression_field(item) for item in value)
    return False


if __name__ == "__main__":
    unittest.main()
