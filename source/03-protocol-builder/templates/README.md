# Protocol Templates

These are repo-owned template protocol shapes. Each folder contains:

- `template.ir.json` - canonical `tecan.protocol_ir.v2` shape with safe example defaults.
- `request.schema.json` - JSON Schema for the template-specific `request.spec.yaml` parameters.
- `examples/` - sample request specs that can be copied into a generation run.

Template IR files intentionally use normal labware and step names such as
`SourcePlate` and `DestinationPlate` instead of FluentControl-specific source
project names. Treat them as starting shapes: copy a template request spec,
fill in project-specific labware, liquid classes, source scripts, and pattern
references, then generate/review `protocol.ir.json`.

The current template set covers:

- `plate_transfer`
- `serial_dilution`
- `normalization`
- `reagent_addition`
- `bead_cleanup`
- `worklist_execution`
- `tip_strategy_test`
