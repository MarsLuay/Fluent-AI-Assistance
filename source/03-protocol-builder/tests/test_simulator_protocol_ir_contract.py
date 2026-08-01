import json
import unittest

from fluent_pipeline.protocol_ir_schema import CURRENT_PROTOCOL_IR_VERSION, protocol_ir_json_schema
from tools.sync_simulator_protocol_ir_contract import DEFAULT_OUTPUT, render_protocol_ir_contract


class SimulatorProtocolIrContractTests(unittest.TestCase):
    def test_checked_in_contract_matches_canonical_schema(self):
        schema = protocol_ir_json_schema()
        rendered = render_protocol_ir_contract()

        self.assertEqual(DEFAULT_OUTPUT.read_text(encoding="utf-8"), rendered)
        self.assertIn(f"export const PROTOCOL_IR_VERSION = {json.dumps(CURRENT_PROTOCOL_IR_VERSION)}", rendered)
        self.assertIn(f"export const PROTOCOL_IR_SCHEMA_ID = {json.dumps(schema['$id'])}", rendered)
        self.assertIn('"aspirate"', rendered)
        self.assertIn('"query_variable"', rendered)

    def test_contract_render_is_deterministic(self):
        self.assertEqual(render_protocol_ir_contract(), render_protocol_ir_contract())


if __name__ == "__main__":
    unittest.main()
