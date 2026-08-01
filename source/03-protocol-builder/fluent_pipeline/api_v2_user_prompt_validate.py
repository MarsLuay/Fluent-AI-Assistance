"""UserPrompt.Validate() prompt_text_quality preflight for API V2 stepped prompts (api-v2-046).

Calls ``Validate()`` on each ``UserPrompt`` command before
``ExecutionChannel.ExecuteCommand`` when a native VisionX API V2 session is
available; otherwise applies prompt_text_quality heuristics (empty/placeholder text)
and surfaces failures as runtime-report ``runtime_errors``.
"""

from __future__ import annotations

from . import xml_compat as ET
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .policies.prompt_text import normalize_operator_prompt_text, prompt_text_is_placeholder
from .api_v2.validate_runtime import (
    runtime_error_for_validate_failure as _shared_runtime_error_for_validate_failure,
)


API_V2_ISSUE_ID = "api-v2-046"
API_V2_METHOD = "UserPrompt.Validate()"
PROMPT_TEXT_QUALITY_SOURCE = "prompt_text_quality"

USER_PROMPT_TYPE_NAMES = frozenset(
    {
        "UserPromptStatement",
        "RUPStandardStatement",
    }
)


@dataclass(frozen=True)
class UserPromptValidateResult:
    ok: bool
    message: str = ""
    reason: str = ""
    prompt_text: str = ""
    source: str = PROMPT_TEXT_QUALITY_SOURCE
    api_v2_method: str = API_V2_METHOD
    api_v2_issue: str = API_V2_ISSUE_ID

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "source": self.source,
            "api_v2_method": self.api_v2_method,
            "api_v2_issue": self.api_v2_issue,
        }
        if self.message:
            payload["message"] = self.message
        if self.reason:
            payload["reason"] = self.reason
        if self.prompt_text:
            payload["prompt_text"] = self.prompt_text
        return payload


class UserPromptCommandLike(Protocol):
    type_name: str
    index: int
    group: str
    payload_xml: str


def is_user_prompt_command(command: UserPromptCommandLike) -> bool:
    return str(command.type_name or "") in USER_PROMPT_TYPE_NAMES


def extract_prompt_message(command: UserPromptCommandLike) -> str:
    """Read operator prompt text from a compiled XSCR command payload."""
    payload = str(command.payload_xml or "").strip()
    if not payload:
        return ""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return ""
    if str(command.type_name or "") == "RUPStandardStatement":
        for node in root.iter():
            if _local_name(node.tag) == "MessageText":
                return (node.text or "").strip()
        for node in root.iter():
            if _local_name(node.tag) == "CheckMessagePrompt":
                return (node.text or "").strip()
        return ""
    for node in root.iter():
        if _local_name(node.tag) == "Prompt":
            return (node.text or "").strip()
    return ""


def validate_user_prompt_offline(command: UserPromptCommandLike) -> UserPromptValidateResult:
    """Offline prompt_text_quality preflight when native ``UserPrompt.Validate()`` is unavailable."""
    if not is_user_prompt_command(command):
        return UserPromptValidateResult(ok=True, source="skipped_non_prompt")

    raw = extract_prompt_message(command)
    if not raw and not str(command.payload_xml or "").strip():
        return UserPromptValidateResult(
            ok=True,
            source="skipped_no_payload",
            message="No prompt payload available for offline UserPrompt validation.",
        )

    normalized = normalize_operator_prompt_text(raw)
    if not normalized:
        return UserPromptValidateResult(
            ok=False,
            reason="empty_prompt",
            message=(
                f"UserPrompt at step {command.index + 1} ({command.type_name} in {command.group}) "
                "has empty operator text (prompt_text_quality / UserPrompt.Validate)."
            ),
            prompt_text=raw,
            source=PROMPT_TEXT_QUALITY_SOURCE,
        )
    if prompt_text_is_placeholder(normalized):
        return UserPromptValidateResult(
            ok=False,
            reason="placeholder_prompt",
            message=(
                f"UserPrompt at step {command.index + 1} ({command.type_name} in {command.group}) "
                f"uses placeholder text {normalized!r} (prompt_text_quality / UserPrompt.Validate)."
            ),
            prompt_text=raw,
            source=PROMPT_TEXT_QUALITY_SOURCE,
        )
    return UserPromptValidateResult(ok=True, prompt_text=raw, source=PROMPT_TEXT_QUALITY_SOURCE)


def validate_user_prompt_before_execute(
    command: UserPromptCommandLike,
    *,
    native_validate: Callable[[], None] | None = None,
) -> UserPromptValidateResult:
    """Validate a UserPrompt command before ``ExecuteCommand`` (native first, then offline)."""
    if not is_user_prompt_command(command):
        return UserPromptValidateResult(ok=True, source="skipped_non_prompt")

    if native_validate is not None:
        try:
            native_validate()
        except Exception as exc:
            offline = validate_user_prompt_offline(command)
            message = str(exc).strip() or "UserPrompt.Validate() failed."
            if not offline.ok:
                message = f"{message}; offline check: {offline.message}"
            return UserPromptValidateResult(
                ok=False,
                reason="native_validate_failed",
                message=message,
                prompt_text=offline.prompt_text or extract_prompt_message(command),
                source="native",
            )
        return UserPromptValidateResult(
            ok=True,
            prompt_text=extract_prompt_message(command),
            source="native",
        )

    return validate_user_prompt_offline(command)


def runtime_error_for_validate_failure(
    result: UserPromptValidateResult,
    command: UserPromptCommandLike,
) -> str:
    return _shared_runtime_error_for_validate_failure(result, command, kind="UserPrompt")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
