import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.prompt_media as prompt_media_module
from tecan_common import native_acceleration
from fluent_pipeline.prompt_media import (
    ensure_compiled_prompt_media_references,
    prompt_media_step_records,
)
from fluent_pipeline.validation import _gate_xscr_ir_roundtrip


class PromptMediaReferenceTests(unittest.TestCase):
    def test_prompt_kind_lowering_uses_native_ascii_and_unicode_fallback(self):
        values = ["STANDARD", "Wörktable", "RUPWorktableStatement"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [prompt_media_module._lower_text(value) for value in values]

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                prompt_media_module,
                "_native_ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_call:
                actual = [prompt_media_module._lower_text(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            native_call.assert_any_call("STANDARD")
            native_call.assert_any_call("Wörktable")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                prompt_media_module, "_native_ascii_lower", return_value=None
            ):
                forced_fallback = [
                    prompt_media_module._lower_text(value) for value in values
                ]
        self.assertEqual(forced_fallback, expected)

    def _ir(self, *, sound_file: str = "media/step022Audio.mp3") -> dict:
        return {
            "protocol": {"name": "Verification_Script2"},
            "steps": [
                {
                    "id": "step_final",
                    "operation": "prompt_user",
                    "parameters": {
                        "prompt": "33/33) You're done!",
                        "image_path": "media/step022Gif.gif",
                        "sound_file": sound_file,
                    },
                }
            ],
        }

    def test_post_compile_fixup_wires_audio_to_matching_prompt(self):
        xscr = """<VxData><Payload><ObjectName>Verification_Script2</ObjectName>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
<RUPStandardStatement><StandardProperties><StandardStatementDataClass>
<SelectedImagePath>C:\\media\\step022Gif.gif</SelectedImagePath>
<MessageText>33/33) You're done!</MessageText><SelectedSoundPath />
</StandardStatementDataClass></StandardProperties></RUPStandardStatement></Object>
</Payload><Checksum></Checksum></VxData>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.xscr"
            path.write_text(xscr, encoding="utf-8")
            ir = self._ir()
            ir["steps"][0]["parameters"]["rup_kind"] = "STANDARD"

            fixups = ensure_compiled_prompt_media_references(path, ir)
            rewritten = path.read_text(encoding="utf-8-sig")

        self.assertEqual(len(fixups), 1)
        self.assertIn(
            r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
            r"\Verification_Script2_media\step022Audio.mp3",
            rewritten,
        )

    def test_post_compile_fixup_does_not_wire_audio_to_earlier_prompt(self):
        xscr = """<VxData><Payload><ObjectName>Verification_Script2</ObjectName>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
<RUPStandardStatement><StandardProperties><StandardStatementDataClass>
<MessageText>4/33) Lock nest 17.</MessageText><SelectedSoundPath />
</StandardStatementDataClass></StandardProperties></RUPStandardStatement></Object>
<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">
<RUPStandardStatement><StandardProperties><StandardStatementDataClass>
<MessageText>33/33) You're done!</MessageText><SelectedSoundPath />
</StandardStatementDataClass></StandardProperties></RUPStandardStatement></Object>
</Payload><Checksum></Checksum></VxData>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.xscr"
            path.write_text(xscr, encoding="utf-8")

            ensure_compiled_prompt_media_references(path, self._ir())
            rewritten = path.read_text(encoding="utf-8-sig")

        first_prompt = rewritten.split("<MessageText>33/33)", 1)[0]
        final_prompt = rewritten.split("<MessageText>33/33)", 1)[1]
        self.assertIn("<SelectedSoundPath />", first_prompt)
        self.assertIn("step022Audio.mp3</SelectedSoundPath>", final_prompt)

    def test_roundtrip_compares_audio_and_media_step_labels(self):
        expected = self._ir()
        actual = self._ir(
            sound_file=(
                r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
                r"\Verification_Script2_media\step022Audio.mp3"
            )
        )
        actual["steps"][0]["parameters"]["image_path"] = (
            r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
            r"\Verification_Script2_media\step022Gif.gif"
        )

        gate = _gate_xscr_ir_roundtrip(expected, actual)

        self.assertEqual(gate["status"], "passed")

    def test_roundtrip_derives_expected_image_from_media_placeholders(self):
        expected = self._ir(sound_file="")
        params = expected["steps"][0]["parameters"]
        params.pop("image_path", None)
        params["media_placeholders"] = [
            {
                "kind": "image",
                "path": "media/step022Image.png",
                "slot": "step022Image",
            },
            {
                "kind": "video",
                "path": "media/step022Gif.gif",
                "slot": "step022Gif",
            },
        ]
        actual = self._ir(sound_file="")
        actual["steps"][0]["parameters"]["image_path"] = (
            r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
            r"\Verification_Script2_media\step022Image.png"
        )

        gate = _gate_xscr_ir_roundtrip(expected, actual)

        self.assertEqual(gate["status"], "passed")

    def test_roundtrip_fails_when_labeled_audio_is_not_wired(self):
        expected = self._ir()
        actual = self._ir(sound_file="")

        gate = _gate_xscr_ir_roundtrip(expected, actual)

        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["details"]["expected"][0]["sound_step_label"], "step022")
        self.assertEqual(gate["details"]["actual"][0]["sound_step_label"], "")

    def test_media_audit_includes_visual_and_audio_operator_labels(self):
        ir = self._ir()
        ir["steps"][0]["parameters"]["media_placeholders"] = [
            {
                "kind": "video",
                "path": "media/step022Gif.gif",
                "slot": "step022Gif",
            }
        ]

        records = prompt_media_step_records(ir)

        self.assertEqual(
            [(item["file"], item["kind"], item["media_step"], item["operator_step"]) for item in records],
            [
                ("step022Gif.gif", "video", "step022", "33/33"),
                ("step022Audio.mp3", "audio", "step022", "33/33"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
