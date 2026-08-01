import unittest

from fluent_pipeline.api_v2.ced import (
    CEDNotificationHandler,
    ced_info_from_text,
    ced_info_from_window,
    handle_common_error_dialog,
    merge_ced_journal_into_report,
    write_button_index,
)
from fluent_pipeline.api_v2.types import CedButton, ICedInfo


class ApiV2CedHandlerTests(unittest.TestCase):
    def test_checksum_dialog_selects_yes_button(self) -> None:
        info = ICedInfo(
            error_id="VX_CHECKSUM",
            title="Checksum mismatch",
            message="Recalculate checksum for imported script?",
            buttons=(
                CedButton(label="Yes", is_safe_default=True),
                CedButton(label="No"),
            ),
        )
        result = handle_common_error_dialog(info)
        self.assertTrue(result.dismissed)
        self.assertFalse(result.fail_gate)
        self.assertEqual(result.button_index, 0)

    def test_hardware_dialog_blocks_gate(self) -> None:
        info = ICedInfo(
            error_id="",
            message="Initialize hardware before continuing?",
            buttons=(CedButton(label="OK"),),
        )
        result = handle_common_error_dialog(info)
        self.assertTrue(result.fail_gate)
        self.assertFalse(result.dismissed)

    def test_ced_notification_invoke_writes_button_index(self) -> None:
        info = ICedInfo(
            error_id="",
            message="Import completed successfully.",
            buttons=(CedButton(label="OK", is_safe_default=True),),
        )
        button_index: list[int] = [-1]
        handler = CEDNotificationHandler()
        result = handler.invoke(info, button_index)
        self.assertTrue(result.dismissed)
        write_button_index(button_index, result.button_index)
        self.assertEqual(button_index[0], 0)
        self.assertEqual(len(handler.journal.entries), 1)

    def test_ced_info_from_window_extracts_buttons(self) -> None:
        window = {
            "title": "Import",
            "children": [
                {"control_type": "Text", "title": "Recalculate checksum?"},
                {"control_type": "Button", "title": "Yes"},
                {"control_type": "Button", "title": "No"},
            ],
        }
        info = ced_info_from_window(window)
        self.assertEqual(len(info.buttons), 2)
        self.assertEqual(info.buttons[0].label, "Yes")

    def test_merge_ced_journal_into_report(self) -> None:
        handler = CEDNotificationHandler()
        handler.invoke(ced_info_from_text("Import completed successfully."), [0])
        report = merge_ced_journal_into_report({"details": {}}, handler.journal)
        self.assertIn("common_error_dialogs", report["details"])
        self.assertEqual(report["details"]["common_error_dialogs"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
