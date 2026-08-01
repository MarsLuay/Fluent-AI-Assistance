"""Compatibility exports for protocol-IR media and prompt helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .media_convert import write_placeholder_video_slot
from .policies.prompt_text import (  # noqa: F401
    MEDIA_PLACEHOLDER_BEGIN,
    MEDIA_PLACEHOLDER_END,
    normalize_operator_prompt_text as _normalize_operator_prompt_text,
    prompt_has_media_boilerplate as _prompt_has_media_boilerplate,
)

CANONICAL_SETUP_GROUP_NAME = "Setup"
RUP_VARIABLE_SELECTOR_INSTRUCTIONS = 'For each test, leave it on "yes" to run it or set it to "no" to skip it.'
RUP_VARIABLE_REVIEW_INSTRUCTIONS = "Review values, then press OK."
RUP_VARIABLE_MAX_INSTRUCTIONS_CHARS = 90

CATALOG_DEFAULT_CLASS_NAMES = frozenset(
    {
        "Plate",
        "Plate96",
        "Plate96Deep",
        "Plate384",
        "Trough",
        "Trough25mL",
        "Trough100mL",
        "Waste",
        "TipBox",
        "MCA100Box",
        "MCA200Box",
        "MCA500Box",
        "FCA50Box",
        "FCA200Box",
        "FCA1000Box",
        "EvaAdapter",
        "MagnetRack",
        "TubeRack",
        "WashStation",
        "WasteChute",
        "Hotel",
        "Adapter",
        "FixedDeck",
        "ExternalLabware",
    }
)

MEDIA_DIR_NAME = "media"
MEDIA_SLOT_EXTENSIONS = {"image": ".png", "video": ".gif"}
PLACEHOLDER_IMAGE_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "templates" / "media" / "placeholder_image.png"
)
PLACEHOLDER_IMAGE_LABEL = "Replace with image"
_MINIMAL_PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63600000020001e221bc3300000000"
    "49454e44ae426082"
)

TOUCHTOOLS_IMAGE_ROOT = r"C:\ProgramData\Tecan\VisionX\TouchToolsData"
TOUCHTOOLS_SCRIPT_FILES_SUBDIR = "Script Files"
DEFAULT_TOUCHTOOLS_IMAGES_DIR = r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
LIQUID_HANDLING_OPERATIONS = frozenset(
    {
        "aspirate",
        "liha_aspirate",
        "dispense",
        "liha_dispense",
        "mix",
        "mca384_mix",
        "liha_mix",
        "wash",
        "pick_up_tips",
        "mca384_get_tips",
        "liha_get_tips",
        "drop_tips",
        "mca384_drop_tips",
        "liha_drop_tips",
        "set_tips_back",
        "detect_liquid",
        "liha_detect_liquid",
    }
)

_EXTERNAL_INIT_ACTION_RE = re.compile(
    r"\b(initiali[sz](?:e|ed|ing|ation)|connected|powered?\s+on|power\s+button)\b",
    re.IGNORECASE,
)
_EXTERNAL_INIT_TARGET_RE = re.compile(
    r"\b(A200|Resolvex|TubeEye|BCR|barcode\s+reader|camera|instrument|device|machine)\b",
    re.IGNORECASE,
)


def is_setup_group_name(name: Any) -> bool:
    """Return true for group names that should collapse into one setup group."""
    lowered = re.sub(r"\s+", " ", str(name or "").strip().casefold())
    if not lowered:
        return False
    if lowered in {
        "setup",
        "operator setup",
        "deck setup",
        "operator deck setup",
        "labware setup",
        "add labware setup",
        "instrument setup",
        "instrument initialization setup",
    }:
        return True
    return lowered.endswith(" setup") or lowered.startswith("setup ")


def canonical_setup_group_name_for_steps(steps: list[dict[str, Any]] | None = None) -> str:
    """Use one stable setup group name for generated FluentControl scripts."""
    if steps:
        for step in steps:
            if isinstance(step, dict) and str(step.get("group") or "").strip().casefold() == "operator setup":
                return str(step.get("group") or CANONICAL_SETUP_GROUP_NAME)
    return CANONICAL_SETUP_GROUP_NAME


def _is_startup_selector_step(step: dict[str, Any]) -> bool:
    if not isinstance(step, dict):
        return False
    if step.get("operation") != "runtime_variable_prompt":
        return False
    name = str(step.get("name") or "").strip().casefold()
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    title = str(params.get("screen_title") or "").strip().casefold()
    return "select verification" in name or "select verification" in title


def normalize_setup_groups(ir: dict[str, Any]) -> dict[str, Any]:
    """Collapse setup-ish IR step groups into one Setup group."""
    steps = ir.get("steps")
    if not isinstance(steps, list):
        return ir
    setup_group = canonical_setup_group_name_for_steps([step for step in steps if isinstance(step, dict)])
    for step in steps:
        if not isinstance(step, dict):
            continue
        group = str(step.get("group") or "")
        if is_setup_group_name(group) or _is_startup_selector_step(step):
            step["group"] = setup_group
    return ir


def normalize_group_hierarchy(ir: dict[str, Any]) -> dict[str, Any]:
    """Preserve a stable group hierarchy hook for legacy callers."""
    return ir


def normalize_runtime_variable_prompt_instructions(ir: dict[str, Any]) -> dict[str, Any]:
    """Normalize runtime variable prompt instructions to concise operator text."""
    steps = ir.get("steps")
    if not isinstance(steps, list):
        return ir
    for step in steps:
        if not isinstance(step, dict) or step.get("operation") != "runtime_variable_prompt":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        raw = str(params.get("instructions") or "").strip()
        if _is_startup_selector_step(step):
            params["instructions"] = RUP_VARIABLE_SELECTOR_INSTRUCTIONS
        elif not raw:
            params["instructions"] = RUP_VARIABLE_REVIEW_INSTRUCTIONS
    return ir


def protocol_is_prompt_only(ir: dict[str, Any] | None) -> bool:
    """True when the protocol has steps but performs no liquid handling."""
    if not isinstance(ir, dict):
        return False
    steps = [step for step in (ir.get("steps") or []) if isinstance(step, dict)]
    if not steps:
        return False
    return not any(
        str(step.get("operation") or "") in LIQUID_HANDLING_OPERATIONS for step in steps
    )


def write_placeholder_image_slot(dest: Path) -> None:
    """Write a valid dummy PNG to ``dest``."""
    template = PLACEHOLDER_IMAGE_TEMPLATE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not template.is_file():
        dest.write_bytes(_MINIMAL_PLACEHOLDER_PNG)
        return
    dest.write_bytes(template.read_bytes())


def media_slot_filename(slot: str, kind: str) -> str:
    ext = MEDIA_SLOT_EXTENSIONS.get(str(kind or "").lower(), ".bin")
    return f"{slot}{ext}"


def media_slot_relative_path(slot: str, kind: str) -> str:
    return f"{MEDIA_DIR_NAME}/{media_slot_filename(slot, kind)}"


def _verification_media_focus(step: dict[str, Any]) -> str:
    group = str(step.get("group") or "").casefold()
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    prompt = str(params.get("prompt") or "").casefold()

    fingers_focus = "RGA fingers parallel and straight vs. diagonal, crossed, or wobbling"
    seating_focus = "correct seating/placement vs. tilt, wiggling, loose seat, or collision risk"
    id_focus = "tube ID pop-up/read and vial-gripper fingers closing evenly"
    cap_focus = "cap removed into the holder and reseated straight and tight"
    camera_focus = "second-camera scan and uncap/recap into the yellow cap holder"
    power_focus = "the A200 power button, screen switch, and initialize sequence"

    def has_word(word: str) -> bool:
        return re.search(rf"\b{word}\b", prompt) is not None

    mentions_power = (
        has_word("power")
        or has_word("initialize")
        or has_word("initialized")
        or has_word("connected")
        or has_word("connect")
    )
    mentions_scan = has_word("scan") or has_word("camera") or has_word("read") or has_word("id")
    mentions_cap = has_word("cap") or has_word("caps") or has_word("uncap") or has_word("recap") or has_word("uncapped")

    if "arm" in group:
        return fingers_focus
    if "a200" in group or "positioning" in group:
        if mentions_power:
            return power_focus
        if mentions_scan and not mentions_cap:
            return id_focus
        return seating_focus
    if "first tube" in group:
        return cap_focus if mentions_cap else id_focus
    if "second tube" in group:
        if mentions_scan and not mentions_cap:
            return camera_focus
        return cap_focus
    if "final" in group or "review" in group:
        return "side-by-side pass vs. fail examples for the whole run"
    if "record" in prompt or "video capture" in prompt:
        return "where to aim the camera so wiggling/misalignment stays visible"
    if mentions_power or "system" in prompt:
        return "the correct system/instrument state before any motion"
    if has_word("finger") or has_word("fingers"):
        return fingers_focus
    if mentions_cap:
        return cap_focus
    return "the correct result vs. common failure (wiggling, misalignment, loose seat)"


def prompt_has_media_boilerplate(text: str) -> bool:
    """Compat facade — owner is ``policies.prompt_text``."""
    return _prompt_has_media_boilerplate(text)


def normalize_operator_prompt_text(text: str) -> str:
    """Compat facade — owner is ``policies.prompt_text``."""
    return _normalize_operator_prompt_text(text)


def prompt_looks_like_external_initialization_check(text: str) -> bool:
    prompt = normalize_operator_prompt_text(text)
    return bool(_EXTERNAL_INIT_ACTION_RE.search(prompt) and _EXTERNAL_INIT_TARGET_RE.search(prompt))


def resolve_verification_prompt_rup_kind(
    params: dict[str, Any],
    *,
    default_rup_kind: str = "mixed",
) -> str:
    mode = str(default_rup_kind or "mixed").strip().lower()
    if mode == "worktable":
        return "worktable"
    if mode == "standard":
        return "standard"
    if params.get("instrument_init_check"):
        return "standard"
    if params.get("deck_presence_check") and prompt_step_worktable_binding(params):
        return "worktable"
    return "standard"


def prompt_step_is_deck_presence_check(params: dict[str, Any]) -> bool:
    return bool(params.get("deck_presence_check")) and bool(prompt_step_worktable_binding(params))


def annotate_verification_prompts_with_media(
    ir: dict[str, Any],
    *,
    default_rup_kind: str = "mixed",
) -> dict[str, Any]:
    mode = str(default_rup_kind or "mixed").strip().lower()
    if mode not in {"standard", "worktable", "mixed"}:
        mode = "mixed"

    annotated_any = False
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("operation") or "") != "prompt_user":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            params = {}
            step["parameters"] = params
        if params.get("plain_prompt"):
            params["prompt"] = normalize_operator_prompt_text(str(params.get("prompt") or ""))
            step["command_id"] = "UserPromptStatement"
            step["name"] = step.get("name") or "Prompt User"
            continue

        step_id = str(step.get("id") or f"step_{step.get('index') or 0:03d}")
        focus = _verification_media_focus(step)
        step_rup_kind = resolve_verification_prompt_rup_kind(params, default_rup_kind=mode)
        placeholders: list[dict[str, Any]] = []
        for kind in ("image", "video"):
            slot = f"{step_id}_{kind}"
            placeholder: dict[str, Any] = {
                "kind": kind,
                "slot": slot,
                "path": media_slot_relative_path(slot, kind),
                "status": "to_attach",
                "focus": f"{'still image' if kind == 'image' else 'motion clip'} showing {focus}",
            }
            if kind == "image" and step_rup_kind == "worktable":
                placeholder["worktable_display"] = True
            placeholders.append(placeholder)
        params["media_placeholders"] = placeholders
        params["media_annotated"] = True
        params["rup_kind"] = step_rup_kind
        params["prompt"] = normalize_operator_prompt_text(str(params.get("prompt") or ""))
        if step_rup_kind == "worktable":
            step["command_id"] = "RUPWorktableStatement"
            step["name"] = step.get("name") or "RUP Worktable"
        else:
            step["command_id"] = "RUPStandardStatement"
            step["name"] = step.get("name") or "RUP Standard"
            params.pop("worktable_labware", None)
            step.pop("target_labware", None)
        annotated_any = True

    if annotated_any:
        assumptions = ir.setdefault("safety_assumptions", [])
        if not any(
            isinstance(item, dict) and item.get("id") == "verification_prompts_expect_media"
            for item in assumptions
        ):
            assumptions.append(
                {
                    "id": "verification_prompts_expect_media",
                    "text": (
                        "Every operator prompt is a teaching/verification step and expects reference "
                        "image and GIF slots to be filled later. Slots are tracked in "
                        "parameters.media_placeholders and media_placeholders.md."
                    ),
                }
            )
    return ir


def force_worktable_prompt_images(ir: dict[str, Any]) -> dict[str, Any]:
    """Route every image-bearing prompt through RUP Worktable.

    RUPWorktableStatement has no sound field. When a legacy Standard prompt
    carried both image/GIF media and audio, preserve the audio with a native
    auto-closing UserPromptStatement immediately before the Worktable prompt.
    """
    steps = ir.get("steps")
    if not isinstance(steps, list):
        return ir

    rewritten_steps: list[Any] = []
    for step in steps:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            rewritten_steps.append(step)
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            params = {}
            step["parameters"] = params
        has_image = bool(
            str(params.get("image_path") or "").strip()
            or prompt_step_worktable_media_path(params)
            or prompt_step_media_path(params)
        )
        if not has_image:
            rewritten_steps.append(step)
            continue
        sound_file = str(
            params.get("sound_file")
            or params.get("sound_path")
            or params.get("selected_sound_path")
            or ""
        ).strip()
        if sound_file and not bool(params.get("worktable_audio_prelude_inserted")):
            prelude_params = {
                "prompt": normalize_operator_prompt_text(str(params.get("prompt") or "Audio cue")),
                "timeout": 1,
                "auto_close": True,
                "sound_file": sound_file,
                "plain_prompt": True,
                "audio_prelude_for_step_id": str(step.get("id") or ""),
            }
            prelude = {
                "id": f"{step.get('id') or 'prompt'}_audio_prelude",
                "operation": "prompt_user",
                "command_id": "UserPromptStatement",
                "name": "Play Prompt Sound",
                "parameters": prelude_params,
            }
            for key in ("group", "source", "source_location"):
                if key in step:
                    prelude[key] = step[key]
            rewritten_steps.append(prelude)
            params["worktable_audio_prelude_inserted"] = True
            params["worktable_audio_prelude_step_id"] = prelude["id"]
        params["rup_kind"] = "worktable"
        params.pop("sound_file", None)
        params.pop("sound_path", None)
        params.pop("selected_sound_path", None)
        params.pop("repeat_sound", None)
        placeholders = params.get("media_placeholders")
        if isinstance(placeholders, list):
            for media in placeholders:
                if not isinstance(media, dict):
                    continue
                media["rup_kind"] = "worktable"
                if str(media.get("kind") or "").lower() == "image":
                    media["worktable_display"] = True
        step["command_id"] = "RUPWorktableStatement"
        step["name"] = step.get("name") or "RUP Worktable"
        rewritten_steps.append(step)
    for index, rewritten in enumerate(rewritten_steps, start=1):
        if isinstance(rewritten, dict):
            rewritten["index"] = index
    ir["steps"] = rewritten_steps
    return ir


def route_unbound_worktable_prompts_to_standard(
    ir: dict[str, Any],
    *,
    allow_standard: bool,
) -> dict[str, Any]:
    """Route legacy unbound Worktable prompts to the Standard prompt renderer.

    A RUPWorktableStatement is only valid when it highlights a concrete deck
    labware item.  Rendering it without ``LabwareName`` creates a script that
    FluentControl 3.7 reports as ``Name must not be empty`` in the Infopad.
    Modern FluentControl supports RUP Standard media prompts, so preserve the
    Worktable renderer only for prompts with an actual deck binding.
    """
    if not allow_standard:
        return ir

    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            continue
        if str(step.get("command_id") or "") != "RUPWorktableStatement":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        binding = prompt_step_worktable_binding(params) or {}
        if str(binding.get("selected_labware_name") or "").strip():
            continue

        params["rup_kind"] = "standard"
        for media in params.get("media_placeholders") or []:
            if not isinstance(media, dict):
                continue
            media["rup_kind"] = "standard"
            media.pop("worktable_display", None)
        step["command_id"] = "RUPStandardStatement"
        if str(step.get("name") or "") == "RUP Worktable":
            step["name"] = "RUP Standard"
        # A variable-indexed binding is removed before rendering because
        # FluentControl cannot resolve it.  It is no longer a deck-presence
        # prompt after that removal, otherwise render_python_draft routes it
        # back to RUP Worktable despite the Standard command id.
        params.pop("deck_presence_check", None)
        params.pop("worktable_labware", None)
        step.pop("target_labware", None)
    return ir


def sync_verification_prompt_target_labware(ir: dict[str, Any]) -> dict[str, Any]:
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        binding = params.get("worktable_labware")
        if not isinstance(binding, dict):
            continue
        labware = str(binding.get("labware") or "").strip()
        if labware and not _labware_name_has_variable_index(labware):
            step["target_labware"] = labware
        elif labware and str(step.get("target_labware") or "").strip() == labware:
            step.pop("target_labware", None)
    return ir


def sanitize_worktable_prompt_variable_labware_bindings(ir: dict[str, Any]) -> dict[str, Any]:
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        binding = params.get("worktable_labware")
        if not isinstance(binding, dict):
            continue
        labware = str(binding.get("labware") or binding.get("selected_labware_name") or "").strip()
        if not labware or not _labware_name_has_variable_index(labware):
            continue
        for key in ("labware", "selected_labware_name", "labware_type", "selected_labware_type"):
            binding.pop(key, None)
        if str(step.get("target_labware") or "").strip() == labware:
            step.pop("target_labware", None)
    return ir


def apply_default_verification_worktable_bindings(
    ir: dict[str, Any],
    *,
    recipe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from .request_spec import recipe_worktable_patterns

    patterns = recipe_worktable_patterns(recipe)
    default = patterns.get("default_prompt")
    if not isinstance(default, dict):
        return ir

    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        if not params.get("deck_presence_check"):
            continue
        if prompt_step_worktable_binding(params):
            continue
        params["worktable_labware"] = dict(default)
    return ir


def collect_media_placeholders(ir: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        placeholders = params.get("media_placeholders")
        if not isinstance(placeholders, list) or not placeholders:
            continue
        rows.append(
            {
                "step_id": step.get("id"),
                "index": step.get("index"),
                "group": step.get("group"),
                "name": step.get("name"),
                "command_id": step.get("command_id"),
                "rup_kind": params.get("rup_kind"),
                "prompt": normalize_operator_prompt_text(str(params.get("prompt") or "")),
                "media": placeholders,
            }
        )
    return rows


def prompt_image_media_slots(ir: dict[str, Any]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "prompt_user":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        image_path = prompt_step_image_path(params)
        if not image_path:
            continue
        image = next(
            (
                media
                for media in (params.get("media_placeholders") or [])
                if isinstance(media, dict) and str(media.get("kind") or "").lower() == "image"
            ),
            None,
        )
        if not image:
            continue
        slots.append(
            {
                "step_id": step.get("id"),
                "slot": str(image.get("slot") or ""),
                "image_path": image_path,
                "prompt": str(params.get("prompt") or ""),
            }
        )
    return slots


def prompt_step_image_path(params: dict[str, Any]) -> str | None:
    return _prompt_step_media_path_by_preference(params, ("image",))


def prompt_step_media_path(params: dict[str, Any]) -> str | None:
    return _prompt_step_media_path_by_preference(params, ("video", "image"))


def prompt_step_worktable_media_path(params: dict[str, Any]) -> str | None:
    if isinstance(params, dict):
        placeholders = params.get("media_placeholders")
        if isinstance(placeholders, list):
            selected = next(
                (
                    media
                    for media in placeholders
                    if isinstance(media, dict)
                    and media.get("worktable_display")
                    and str(media.get("slot") or "").strip()
                ),
                None,
            )
            if selected:
                kind = str(selected.get("kind") or "media").lower()
                slot = str(selected.get("slot") or "").strip()
                return str(selected.get("path") or media_slot_relative_path(slot, kind))
    return _prompt_step_media_path_by_preference(params, ("image", "video"))


def _prompt_step_media_path_by_preference(
    params: dict[str, Any],
    preferred_kinds: tuple[str, ...],
) -> str | None:
    if not isinstance(params, dict):
        return None
    placeholders = params.get("media_placeholders")
    if not isinstance(placeholders, list):
        return None
    for wanted_kind in preferred_kinds:
        media = next(
            (
                item
                for item in placeholders
                if isinstance(item, dict) and str(item.get("kind") or "").lower() == wanted_kind
            ),
            None,
        )
        if not media:
            continue
        slot = str(media.get("slot") or "").strip()
        if not slot:
            continue
        return str(media.get("path") or media_slot_relative_path(slot, wanted_kind))
    return None


def prompt_step_worktable_binding(params: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(params, dict):
        return None
    binding = params.get("worktable_labware")
    if not isinstance(binding, dict):
        return None
    labware = str(binding.get("labware") or binding.get("selected_labware_name") or "").strip()
    labware_type = str(binding.get("labware_type") or binding.get("selected_labware_type") or "").strip()
    grid = binding.get("grid")
    site = binding.get("site")
    if not labware and grid is None and site is None:
        return None
    resolved: dict[str, Any] = {}
    if labware and not _labware_name_has_variable_index(labware):
        resolved["selected_labware_name"] = labware
    if labware_type and resolved.get("selected_labware_name"):
        resolved["selected_labware_type"] = labware_type
    if grid is not None:
        resolved["grid"] = grid
    if site is not None:
        resolved["site"] = site
    return resolved or None


def _labware_name_has_variable_index(value: str) -> bool:
    for bracket_value in re.findall(r"\[([^\]]+)\]", str(value or "")):
        if not re.fullmatch(r"\d+", bracket_value.strip()):
            return True
    return False


def _media_slot_specs_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for media in row.get("media", []):
            if not isinstance(media, dict):
                continue
            slot = str(media.get("slot") or "").strip()
            if not slot:
                continue
            kind = str(media.get("kind") or "media").lower()
            specs.append(
                {
                    "filename": media_slot_filename(slot, kind),
                    "path": str(media.get("path") or media_slot_relative_path(slot, kind)),
                    "slot": slot,
                    "kind": kind,
                    "step_id": row.get("step_id"),
                    "group": row.get("group"),
                    "name": row.get("name"),
                    "command_id": row.get("command_id"),
                    "rup_kind": row.get("rup_kind"),
                    "prompt": row.get("prompt"),
                    "focus": media.get("focus"),
                    "worktable_display": bool(media.get("worktable_display")),
                    "worktable_safe": bool(media.get("worktable_safe")),
                }
            )
    return specs


def media_slot_specs(ir: dict[str, Any]) -> list[dict[str, Any]]:
    return _media_slot_specs_from_rows(collect_media_placeholders(ir))


def sound_path_specs_from_ir(ir: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        params = step.get("parameters")
        if not isinstance(params, dict):
            continue
        sound_file = str(params.get("sound_file") or "").strip()
        if not sound_file:
            continue
        basename = Path(sound_file).name
        slot = Path(basename).stem or str(step.get("id") or "audio")
        specs.append(
            {
                "filename": basename,
                "path": sound_file,
                "slot": slot,
                "kind": "audio",
                "step_id": step.get("id"),
                "group": step.get("group"),
                "name": step.get("name"),
                "command_id": step.get("command_id"),
            }
        )
    return specs


def _join_windows_path(*parts: str | None) -> str:
    segments: list[str] = []
    for index, part in enumerate(parts):
        if part is None:
            continue
        text = str(part).strip().replace("/", "\\")
        if not text:
            continue
        if index == 0:
            segments.append(text.rstrip("\\"))
        else:
            trimmed = text.strip("\\")
            if trimmed:
                segments.append(trimmed)
    return "\\".join(segments)


def deployed_media_path(
    touchtools_dir: str,
    filename: str,
    *,
    subfolder: str | None = None,
) -> str:
    return _join_windows_path(touchtools_dir, subfolder, filename)


def _group_media_slot_specs_by_step(
    specs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("step_id") or spec.get("slot") or "")
        grouped.setdefault(key, []).append(spec)
    return grouped


def _media_spec_rup_kind(spec: Mapping[str, Any]) -> str:
    raw = str(spec.get("rup_kind") or "").strip().lower()
    if raw in {"standard", "worktable"}:
        return raw
    command_id = str(spec.get("command_id") or "").strip()
    if command_id == "RUPWorktableStatement":
        return "worktable"
    if command_id == "RUPStandardStatement":
        return "standard"
    return ""


def _prompt_display_media_slots(
    specs_by_step: Mapping[str, list[dict[str, Any]]],
) -> tuple[set[str], set[str]]:
    selected_image_path_slots: set[str] = set()
    worktable_detail_slots: set[str] = set()
    for grouped in specs_by_step.values():
        rup_kinds = {_media_spec_rup_kind(item) for item in grouped}
        rup_kinds.discard("")
        if rup_kinds == {"worktable"}:
            worktable_selected = next((item for item in grouped if item.get("worktable_display")), None)
            if worktable_selected is None:
                worktable_selected = next(
                    (item for item in grouped if str(item.get("kind") or "").lower() == "image"),
                    None,
                )
            if worktable_selected is None:
                worktable_selected = next(
                    (item for item in grouped if str(item.get("kind") or "").lower() == "video"),
                    None,
                )
            if worktable_selected is not None:
                worktable_detail_slots.add(str(worktable_selected.get("slot") or ""))
            continue
        if rup_kinds == {"standard"}:
            standard_selected = next(
                (item for item in grouped if str(item.get("kind") or "").lower() == "video"),
                None,
            )
            if standard_selected is None:
                standard_selected = next(
                    (item for item in grouped if str(item.get("kind") or "").lower() == "image"),
                    None,
                )
            if standard_selected is not None:
                selected_image_path_slots.add(str(standard_selected.get("slot") or ""))
            continue

        standard_selected = next(
            (item for item in grouped if str(item.get("kind") or "").lower() == "video"),
            None,
        )
        if standard_selected is None:
            standard_selected = next(
                (item for item in grouped if str(item.get("kind") or "").lower() == "image"),
                None,
            )
        if standard_selected is not None:
            selected_image_path_slots.add(str(standard_selected.get("slot") or ""))
        worktable_selected = next(
            (item for item in grouped if str(item.get("kind") or "").lower() == "image"),
            None,
        )
        if worktable_selected is None:
            worktable_selected = next(
                (item for item in grouped if str(item.get("kind") or "").lower() == "video"),
                None,
            )
        if worktable_selected is not None:
            worktable_detail_slots.add(str(worktable_selected.get("slot") or ""))
    return selected_image_path_slots, worktable_detail_slots


def worktable_pipeline_video_slots(specs: list[dict[str, Any]]) -> set[str]:
    filtered = [spec for spec in specs or [] if isinstance(spec, dict)]
    if not filtered:
        return set()
    _, worktable_detail_slots = _prompt_display_media_slots(_group_media_slot_specs_by_step(filtered))
    return {
        str(spec.get("slot") or "").strip()
        for spec in filtered
        if str(spec.get("kind") or "").lower() == "video"
        and str(spec.get("slot") or "").strip() in worktable_detail_slots
    }


def required_media_slot_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [spec for spec in specs or [] if isinstance(spec, dict)]
    if not filtered:
        return []
    selected, worktable = _prompt_display_media_slots(_group_media_slot_specs_by_step(filtered))
    required = selected | worktable
    return [spec for spec in filtered if str(spec.get("slot") or "").strip() in required]


def build_media_path_map_from_specs(
    specs: list[dict[str, Any]],
    touchtools_dir: str,
    *,
    subfolder: str | None = None,
    protocol_name: str | None = None,
) -> dict[str, Any]:
    base = str(touchtools_dir or "").strip()
    sub = str(subfolder).strip() if subfolder else None
    specs = [spec for spec in specs or [] if isinstance(spec, dict)]
    specs_by_step = _group_media_slot_specs_by_step(specs)
    selected_image_path_slots, worktable_detail_slots = _prompt_display_media_slots(specs_by_step)
    entries: list[dict[str, Any]] = []
    for spec in specs:
        kind = str(spec.get("kind") or "media").lower()
        slot = str(spec.get("slot") or "").strip()
        filename = spec.get("filename") or media_slot_filename(slot, kind)
        bundle_relative = spec.get("path") or media_slot_relative_path(slot, kind)
        drives_worktable_detail_path = slot in worktable_detail_slots
        drives_selected_image_path = slot in selected_image_path_slots
        drives_selected_sound_path = kind == "audio"
        entries.append(
            {
                "slot": slot,
                "kind": kind,
                "filename": filename,
                "bundle_relative_path": bundle_relative,
                "absolute_path": deployed_media_path(base, filename, subfolder=sub),
                "drives_selected_image_path": drives_selected_image_path,
                "drives_worktable_detail_path": drives_worktable_detail_path,
                "drives_selected_sound_path": drives_selected_sound_path,
                "attachment_only": not (
                    drives_worktable_detail_path or drives_selected_image_path or drives_selected_sound_path
                ),
                "step_id": spec.get("step_id"),
                "group": spec.get("group"),
                "name": spec.get("name"),
                "command_id": spec.get("command_id"),
                "rup_kind": spec.get("rup_kind"),
                "focus": spec.get("focus"),
                "worktable_display": bool(spec.get("worktable_display")),
                "worktable_safe": bool(spec.get("worktable_safe")),
            }
        )
    entries.sort(key=lambda entry: (str(entry.get("slot") or ""), str(entry.get("kind") or "")))
    return {
        "protocol": protocol_name,
        "touchtools_dir": base,
        "subfolder": sub,
        "media_dir": MEDIA_DIR_NAME,
        "image_count": sum(1 for entry in entries if entry["kind"] == "image"),
        "video_count": sum(1 for entry in entries if entry["kind"] == "video"),
        "audio_count": sum(1 for entry in entries if entry["kind"] == "audio"),
        "entries": entries,
    }


def build_media_path_map(
    ir: dict[str, Any],
    touchtools_dir: str,
    *,
    subfolder: str | None = None,
) -> dict[str, Any]:
    protocol = ir.get("protocol", {}) if isinstance(ir.get("protocol"), dict) else {}
    specs = media_slot_specs(ir)
    specs.extend(sound_path_specs_from_ir(ir))
    return build_media_path_map_from_specs(
        specs,
        touchtools_dir,
        subfolder=subfolder,
        protocol_name=protocol.get("name"),
    )


def build_media_path_map_from_placeholder_rows(
    rows: list[dict[str, Any]],
    touchtools_dir: str,
    *,
    subfolder: str | None = None,
    protocol_name: str | None = None,
) -> dict[str, Any]:
    return build_media_path_map_from_specs(
        _media_slot_specs_from_rows(rows),
        touchtools_dir,
        subfolder=subfolder,
        protocol_name=protocol_name,
    )


def resolve_touchtools_images_dir() -> str:
    import os

    for env_name in ("TECAN_TOUCHTOOLS_IMAGES_DIR", "TECAN_TOUCHTOOLS_DIR"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            return raw.rstrip("\\/")
    return DEFAULT_TOUCHTOOLS_IMAGES_DIR


def touchtools_media_subfolder(script_name: str) -> str:
    """Per-script folder under TouchTools Images."""
    name = str(script_name or "").strip() or "script"
    keep: list[str] = []
    for char in name:
        if char.isalnum() or char in "._-":
            keep.append(char)
        elif char.isspace():
            keep.append("_")
        else:
            keep.append("_")
    sanitized = "".join(keep).strip("._-") or "script"
    suffix = "_media"
    if sanitized.casefold().endswith(suffix):
        return sanitized
    return f"{sanitized}{suffix}"


def resolve_touchtools_media_subfolder(ir: dict[str, Any]) -> str:
    """Derive the TouchTools deploy subfolder for one protocol IR."""
    protocol = ir.get("protocol", {}) if isinstance(ir.get("protocol"), dict) else {}
    name = str(protocol.get("name") or "").strip()
    if not name:
        source = ir.get("source", {}) if isinstance(ir.get("source"), dict) else {}
        naming = source.get("script_naming", {}) if isinstance(source.get("script_naming"), dict) else {}
        name = str(naming.get("final_name") or naming.get("requested_name") or "").strip()
    return touchtools_media_subfolder(name or "script")


def render_media_path_map_markdown(path_map: dict[str, Any]) -> str:
    name = path_map.get("protocol") or "Generated protocol"
    base = path_map.get("touchtools_dir") or "<TouchTools image directory>"
    sub = path_map.get("subfolder")
    entries = path_map.get("entries") or []
    target = _join_windows_path(base, sub) if sub else base
    lines = [
        f"# Deployed Media Path Map: {name}",
        "",
        f"- Target TouchTools directory: `{target}`",
        f"- Image slots: `{path_map.get('image_count', 0)}`",
        f"- Video/GIF slots: `{path_map.get('video_count', 0)}`",
        f"- Audio slots: `{path_map.get('audio_count', 0)}`",
        "",
        "## Mapping",
        "",
    ]
    if not entries:
        lines.append("- No operator prompts required media.")
        return "\n".join(lines) + "\n"
    for entry in entries:
        roles: list[str] = []
        if entry.get("drives_selected_image_path"):
            roles.append("display -> SelectedImagePath")
        if entry.get("drives_worktable_detail_path"):
            roles.append("display -> CustomDetailImageFilePath")
        if entry.get("drives_selected_sound_path"):
            roles.append("display -> SelectedSoundPath/SoundFile")
        role = "; ".join(roles) if roles else "available reference media"
        lines.append(
            f"- `{entry.get('bundle_relative_path')}` -> `{entry.get('absolute_path')}` ({role})"
        )
    return "\n".join(lines) + "\n"


def apply_media_path_map_to_xscr(xscr_path: Path, path_map: dict[str, Any]) -> list[dict[str, str]]:
    """Rewrite bundle-relative prompt media paths to deployed TouchTools absolutes."""
    if not xscr_path.exists():
        return []
    try:
        text = xscr_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    fixups: list[dict[str, str]] = []
    updated = text
    for entry in path_map.get("entries") or []:
        bundle_path = str(entry.get("bundle_relative_path") or "").strip()
        absolute_path = str(entry.get("absolute_path") or "").strip()
        if not bundle_path or not absolute_path or bundle_path not in updated:
            continue
        for tag in ("CustomDetailImageFilePath", "SelectedImagePath", "File"):
            pattern = re.compile(rf"(<{tag}>){re.escape(bundle_path)}(</{tag}>)")
            if not pattern.search(updated):
                continue

            def _replace(match: re.Match[str], *, target: str = absolute_path) -> str:
                return f"{match.group(1)}{target}{match.group(2)}"

            updated = pattern.sub(_replace, updated)
            fixups.append({"from": bundle_path, "to": absolute_path, "tag": tag})
        if entry.get("drives_selected_sound_path") or entry.get("kind") == "audio":
            for tag in ("SelectedSoundPath", "SoundFile"):
                pattern = re.compile(rf"(<{tag}>){re.escape(bundle_path)}(</{tag}>)")
                if not pattern.search(updated):
                    continue

                def _replace_sound(match: re.Match[str], *, target: str = absolute_path) -> str:
                    return f"{match.group(1)}{target}{match.group(2)}"

                updated = pattern.sub(_replace_sound, updated)
                fixups.append({"from": bundle_path, "to": absolute_path, "tag": tag})
    if updated != text:
        xscr_path.write_text(updated, encoding="utf-8")
    return fixups


TOUCHTOOLS_MEDIA_PATH_TAGS = (
    "CustomDetailImageFilePath",
    "SelectedImagePath",
    "SelectedSoundPath",
    "SoundFile",
    "File",
)


def rewrite_flat_touchtools_media_paths_in_xscr(
    xscr_path: Path,
    *,
    touchtools_dir: str,
    subfolder: str,
    filenames: list[str],
) -> list[dict[str, str]]:
    """Insert a per-script subfolder into already-absolute TouchTools media paths."""
    if not xscr_path.exists() or not subfolder or not filenames:
        return []
    try:
        text = xscr_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    base = str(touchtools_dir or "").strip()
    sub = str(subfolder).strip()
    if not base or not sub:
        return []
    fixups: list[dict[str, str]] = []
    updated = text
    for filename in filenames:
        name = str(filename or "").strip()
        if not name:
            continue
        flat_path = deployed_media_path(base, name)
        target_path = deployed_media_path(base, name, subfolder=sub)
        if flat_path == target_path or flat_path not in updated:
            continue
        for tag in TOUCHTOOLS_MEDIA_PATH_TAGS:
            pattern = re.compile(rf"(<{tag}>){re.escape(flat_path)}(</{tag}>)")
            if not pattern.search(updated):
                continue

            def _replace(match: re.Match[str], *, target: str = target_path) -> str:
                return f"{match.group(1)}{target}{match.group(2)}"

            updated = pattern.sub(_replace, updated)
            fixups.append({"from": flat_path, "to": target_path, "tag": tag})
    if updated != text:
        xscr_path.write_text(updated, encoding="utf-8")
    return fixups


def apply_touchtools_media_path_map_to_xscr(
    xscr_path: Path,
    path_map: dict[str, Any],
) -> list[dict[str, str]]:
    """Rewrite bundle-relative and flat absolute prompt media paths in one XSCR."""
    fixups = apply_media_path_map_to_xscr(xscr_path, path_map)
    sub = str(path_map.get("subfolder") or "").strip()
    base = str(path_map.get("touchtools_dir") or "").strip()
    if not sub or not base:
        return fixups
    filenames = [
        str(entry.get("filename") or "").strip()
        for entry in path_map.get("entries") or []
        if str(entry.get("filename") or "").strip()
    ]
    fixups.extend(
        rewrite_flat_touchtools_media_paths_in_xscr(
            xscr_path,
            touchtools_dir=base,
            subfolder=sub,
            filenames=filenames,
        )
    )
    return fixups


def apply_deployed_touchtools_media_paths(
    xscr_path: Path,
    ir: dict[str, Any],
    *,
    touchtools_dir: str | None = None,
    subfolder: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Rewrite bundle-relative prompt media paths to deployed TouchTools absolutes."""
    if not collect_media_placeholders(ir) and not sound_path_specs_from_ir(ir):
        return {}, []
    base = str(touchtools_dir or resolve_touchtools_images_dir()).strip()
    resolved_subfolder = subfolder if subfolder is not None else resolve_touchtools_media_subfolder(ir)
    path_map = build_media_path_map(ir, base, subfolder=resolved_subfolder)
    fixups = apply_touchtools_media_path_map_to_xscr(xscr_path, path_map)
    return path_map, fixups
