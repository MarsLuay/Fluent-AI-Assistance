"""Tests for image-capable operator prompts.

A media prompt (``UserPromptStep`` with ``image_path``) must compile to a
TouchTools ``RUPWorktableStatement`` whose ``CustomDetailImageFilePath`` renders
detail media on the worktable prompt path. A plain prompt (no ``image_path``)
must keep emitting the standard ``UserPromptStatement`` — no behavior change.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.compiler.renderer import Renderer  # noqa: E402
from fluentcoder.decompiler import parse_xscr  # noqa: E402
from fluentcoder.ir.schema import Group, Protocol, UserPromptStep  # noqa: E402
from fluentcoder.worktable import Worktable  # noqa: E402
from tests.conftest import bind_offline_authoring  # noqa: E402


PLAIN_STATEMENT = "Tecan.Core.Scripting.UserPromptStatement"
IMAGE_STATEMENT = "Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement"
WORKTABLE_STATEMENT = "Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement"


def _protocol(*steps: UserPromptStep) -> Protocol:
    return Protocol(
        name="Prompt Image Test",
        worktable_guid="00000000-0000-0000-0000-000000000001",
        worktable_name="WT",
        groups=[Group(name="Steps", steps=list(steps))],
    )


def test_plain_prompt_emits_user_prompt_statement():
    xml = Renderer().render(_protocol(UserPromptStep(prompt="Plain prompt")))
    assert PLAIN_STATEMENT in xml
    assert IMAGE_STATEMENT not in xml
    assert "<Prompt>Plain prompt</Prompt>" in xml


def test_plain_prompt_can_play_sound_file():
    xml = Renderer().render(
        _protocol(
            UserPromptStep(
                prompt="Audio cue",
                timeout=1,
                auto_close=True,
                sound_path=r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Script_media\step001.mp3",
            )
        )
    )
    assert PLAIN_STATEMENT in xml
    assert "<AutoClose>True</AutoClose>" in xml
    assert "<Timeout>1</Timeout>" in xml
    assert (
        "<SoundFile>C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\Script_media\\step001.mp3</SoundFile>"
        in xml
    )


def test_plain_prompt_wait_for_operator_uses_minimum_timeout_one():
    xml = Renderer().render(_protocol(UserPromptStep(prompt="Wait for operator", timeout=0)))
    assert "<AutoClose>False</AutoClose>" in xml
    assert "<Timeout>1</Timeout>" in xml
    assert "<Timeout>0</Timeout>" not in xml


def test_plain_multiline_prompt_preserves_text_without_xml_indentation():
    prompt = "First line\n\n  1. Indented instruction\nSecond line"
    xml = Renderer().render(_protocol(UserPromptStep(prompt=prompt)))
    root = ET.fromstring(xml)
    parsed = next(element.text for element in root.iter() if element.tag == "Prompt")

    assert parsed == prompt


def test_image_prompt_emits_rup_worktable_statement():
    xml = Renderer().render(
        _protocol(UserPromptStep(prompt="Look here", image_path="media/step_009_image.png"))
    )
    assert WORKTABLE_STATEMENT in xml
    assert IMAGE_STATEMENT not in xml
    assert PLAIN_STATEMENT not in xml
    assert "<CustomDetailImageFilePath>media/step_009_image.png</CustomDetailImageFilePath>" in xml
    assert "<LabwareDescriptionGui>Look here</LabwareDescriptionGui>" in xml
    assert "<Grid>0</Grid>" in xml
    assert "<Site>0</Site>" in xml


def test_empty_image_path_is_graceful():
    # An empty image path still emits a valid RUPWorktableStatement with an
    # empty CustomDetailImageFilePath, so a missing capture never breaks load.
    xml = Renderer().render(
        _protocol(UserPromptStep(prompt="No image yet", image_path=""))
    )
    assert WORKTABLE_STATEMENT in xml
    assert "<CustomDetailImageFilePath></CustomDetailImageFilePath>" in xml


def test_mixed_prompts_keep_plain_unchanged():
    xml = Renderer().render(
        _protocol(
            UserPromptStep(prompt="Plain"),
            UserPromptStep(prompt="Image", image_path="media/step_001_image.png"),
        )
    )
    assert xml.count(f'<Object Type="{PLAIN_STATEMENT}">') == 1
    assert xml.count(f'<Object Type="{WORKTABLE_STATEMENT}">') == 1


def test_worktable_helper_passes_image_path():
    wt = bind_offline_authoring(Worktable(name="Helper Test"), with_device=False)
    wt.user_prompt("With image", image_path="media/step_003_image.png")
    protocol = wt.to_protocol()
    [step] = protocol.groups[0].steps
    assert isinstance(step, UserPromptStep)
    assert step.image_path == "media/step_003_image.png"
    assert step.rup_kind == "worktable"


def test_worktable_helper_emits_rup_worktable_statement_with_detail_media():
    wt = bind_offline_authoring(Worktable(name="Helper Test"), with_device=False)
    wt.user_prompt_worktable(
        prompt="Watch the motion",
        image_path="media/step_003_video.gif",
    )
    xml = Renderer().render(wt.to_protocol())
    assert WORKTABLE_STATEMENT in xml
    assert IMAGE_STATEMENT not in xml
    assert "<CustomDetailImageFilePath>media/step_003_video.gif</CustomDetailImageFilePath>" in xml
    assert "<IsCustomDetailImageUsed>true</IsCustomDetailImageUsed>" in xml
    assert "<LabwareDescriptionGui>Watch the motion</LabwareDescriptionGui>" in xml


def test_image_statement_round_trips(tmp_path):
    # Compile -> .xscr -> decompile -> re-render. RUPWorktableStatement decodes
    # to UserPromptStep so CustomDetailImageFilePath survives via image_path.
    renderer = Renderer()
    protocol = _protocol(
        UserPromptStep(prompt="Look here", image_path="media/step_009_image.png")
    )
    xscr = renderer.render_to_file(protocol, tmp_path / "orig.xscr")
    reparsed = parse_xscr(xscr)
    step = reparsed.groups[0].steps[0]
    assert isinstance(step, UserPromptStep)
    assert step.prompt == "Look here"
    assert step.image_path == "media/step_009_image.png"
    assert step.rup_kind == "worktable"
    re_rendered = renderer.render(reparsed)
    assert WORKTABLE_STATEMENT in re_rendered
    assert "<CustomDetailImageFilePath>media/step_009_image.png</CustomDetailImageFilePath>" in re_rendered


def test_standard_image_prompt_still_emits_rup_standard_statement():
    xml = Renderer().render(
        _protocol(
            UserPromptStep(
                prompt="Legacy standard",
                image_path="media/step_009_image.png",
                rup_kind="standard",
            )
        )
    )
    assert IMAGE_STATEMENT in xml
    assert WORKTABLE_STATEMENT not in xml
    assert "<SelectedImagePath>media/step_009_image.png</SelectedImagePath>" in xml


def test_empty_standard_prompt_preserves_standard_rup_kind():
    wt = bind_offline_authoring(Worktable(name="Standard prompt"), with_device=False)
    wt.user_prompt("Completed", image_path="", rup_kind="standard")

    [step] = wt.to_protocol().groups[0].steps
    assert step.rup_kind == "standard"
    xml = Renderer().render(wt.to_protocol())
    assert IMAGE_STATEMENT in xml
    assert WORKTABLE_STATEMENT not in xml

