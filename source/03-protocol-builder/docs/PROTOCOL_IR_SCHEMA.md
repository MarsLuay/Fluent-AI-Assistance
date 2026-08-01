# Protocol IR Schema: tecan.protocol_ir.v2

- Schema ID: `https://fluent-ai-assistance.local/schemas/protocol_ir.v2.schema.json`
- JSON Schema draft: `https://json-schema.org/draft/2020-12/schema`
- Additional root properties are allowed for forward-compatible annotations.

## Required Root Fields

- `ir_version`: constant `tecan.protocol_ir.v2`
- `id`: string
- `protocol`: protocol
- `source`: source
- `worktable`: worktable
- `labware`: array
- `reagents`: array
- `liquid_classes`: array
- `variables`: array
- `worklists`: array
- `dependencies`: array
- `safety_assumptions`: array
- `steps`: array

## Typed Expression Fields

- Expression schema version: `fluent_control.expression.v1`.
- Variable defaults: `variables[].default_expression`.
- Variable assignments: `steps[].parameters.value_expression` and `variables[].assignments[].value_expression`.
- Conditions: `steps[].parameters.condition_expression`.
- Volumes: `steps[].volume_ul_expression` and `steps[].parameters.volume_ul_expression`.
- Positions and loop-like counts: `*_expression` companions such as `position_expression`, `site_expression`, `destination_site_expression`, `loop_count_expression`, and `number_of_loops_expression`.
- Legacy scalar fields may remain for compatibility, but the `*_expression` field is the canonical v2 value when present.


## Operation Enum

- `add_labware`: Add Labware; requires labware target.
- `load_labware`: Load Labware; requires labware target.
- `initialize_device`: Initialize Device; no extra required fields.
- `move_plate`: Move Plate; requires labware target.
- `get_head_adapter`: Mount Head Adapter; no extra required fields.
- `drop_head_adapter`: Drop Head Adapter; no extra required fields.
- `pick_up_tips`: Pick Up Tips; requires labware target.
- `set_tips_back`: Return Tips; requires labware target.
- `drop_tips`: Drop Tips; requires labware target.
- `mca384_get_tips`: Pick Up Tips; requires labware target.
- `mca384_drop_tips`: Drop Tips; requires labware target.
- `liha_get_tips`: LiHa Get Tips; requires labware target.
- `liha_drop_tips`: LiHa Drop Tips; requires labware target.
- `aspirate`: Aspirate; requires labware target, volume_ul, liquid_class.
- `dispense`: Dispense; requires labware target, volume_ul, liquid_class.
- `mix`: Mix; requires labware target, volume_ul, liquid_class.
- `mca384_mix`: Mix; requires labware target, volume_ul, liquid_class.
- `liha_aspirate`: LiHa Aspirate; requires labware target, volume_ul, liquid_class.
- `liha_dispense`: LiHa Dispense; requires labware target, volume_ul, liquid_class.
- `liha_mix`: LiHa Mix; requires labware target, volume_ul, liquid_class.
- `wash`: Wash; no extra required fields.
- `read_worklist`: Read Worklist; requires labware target.
- `loop_over_wells`: Loop Over Wells; no extra required fields.
- `conditional_branch`: Conditional Branch; no extra required fields.
- `default_branch`: Default Branch; no extra required fields.
- `query_variable`: Query Variable; no extra required fields.
- `set_variable`: Set Variable; no extra required fields.
- `set_remaining_runtime`: Set Remaining Runtime; no extra required fields.
- `runtime_variable_prompt`: Runtime Variable Prompt; no extra required fields.
- `prompt_user`: Prompt User; no extra required fields.
- `call_subroutine`: Call Subroutine; no extra required fields.
- `execute_application`: Execute Application; no extra required fields.
- `execute_vb_script`: Execute VBScript; no extra required fields.
- `application_driver_macro`: Application Driver Macro; no extra required fields.
- `comment`: Comment; no extra required fields.

## Migrations

- Current protocol version: `tecan.protocol_ir.v2`
- Register future migrations with `register_protocol_ir_migration(from_version, to_version, migrator, schema=...)`.
- Migrations run one version edge at a time and must return a JSON-serializable dict.
