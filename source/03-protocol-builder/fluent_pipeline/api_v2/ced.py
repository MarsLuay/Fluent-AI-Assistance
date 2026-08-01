"""Common Error Dialog handling (api-v2-035, api-v2-042).

``CEDNotification.Invoke(ICedInfo cedInfo, ref Int32 buttonIndex)`` is the
handler signature for ``IRuntimeControllerEvents.CommonErrorDialog``. Gate 27
providers subscribe (or emulate via structured parsing) so unattended import/load
checks can set ``buttonIndex`` for safe dialogsâ€”checksum recalc, import OKâ€”
instead of scraping FluentControl window text with pywinauto regex alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableSequence, Sequence

from .types import CedButton, CedHandlerResult, ICedInfo

_SAFE_DIALOG_RE = re.compile(
    r"(checksum|recalculate|import completed|successfully imported|operation completed|already exists|ok|yes)",
    re.IGNORECASE,
)
_UNSAFE_DIALOG_RE = re.compile(
    r"(delete|remove|hardware|initialize|home|move|run method|start instrument|destructive)",
    re.IGNORECASE,
)
_MISSING_REFERENCE_RE = re.compile(
    r"(missing|not found|unresolved).{0,80}(reference|file|object|dependency)",
    re.IGNORECASE,
)
_CED_DETAIL_KEY = "common_error_dialogs"


@dataclass
class CommonErrorDialogJournal:
    """Records every ``CommonErrorDialog`` / CEDNotification decision."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, ced_info: ICedInfo, result: CedHandlerResult, *, source: str) -> None:
        self.entries.append(
            {
                "source": source,
                "error_id": ced_info.error_id,
                "title": ced_info.title,
                "message": ced_info.message,
                "buttons": [button.label for button in ced_info.buttons],
                "button_index": result.button_index,
                "dismissed": result.dismissed,
                "fail_gate": result.fail_gate,
                "log_message": result.log_message,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {"entries": list(self.entries), "count": len(self.entries)}


class CEDNotificationHandler:
    """Python stand-in for ``Tecan.VisionX.API.V2.CEDNotification.Invoke``."""

    def __init__(self, journal: CommonErrorDialogJournal | None = None):
        self.journal = journal or CommonErrorDialogJournal()

    def invoke(self, ced_info: ICedInfo | Any, button_index: Any) -> CedHandlerResult:
        """Handle ``CommonErrorDialog`` and write the chosen ``buttonIndex``."""
        info = ced_info if isinstance(ced_info, ICedInfo) else ced_info_from_dotnet(ced_info)
        result = handle_common_error_dialog(info)
        write_button_index(button_index, result.button_index)
        self.journal.record(info, result, source="IRuntimeControllerEvents.CommonErrorDialog")
        return result

    def __call__(self, ced_info: ICedInfo | Any, button_index: Any) -> CedHandlerResult:
        return self.invoke(ced_info, button_index)


def handle_common_error_dialog(ced_info: ICedInfo) -> CedHandlerResult:
    """Select a safe button index for unattended Gate 27 checks."""
    combined = " ".join(
        part for part in (ced_info.title, ced_info.message, ced_info.error_id) if part
    )
    if _MISSING_REFERENCE_RE.search(combined):
        return CedHandlerResult(
            button_index=-1,
            dismissed=False,
            fail_gate=True,
            log_message=f"CommonErrorDialog missing reference: {ced_info.message}",
        )
    if _UNSAFE_DIALOG_RE.search(combined):
        return CedHandlerResult(
            button_index=-1,
            dismissed=False,
            fail_gate=True,
            log_message=f"CommonErrorDialog blocked unattended run: {ced_info.message}",
        )

    buttons = list(ced_info.buttons)
    if not buttons:
        if _SAFE_DIALOG_RE.search(combined):
            return CedHandlerResult(
                button_index=0,
                dismissed=True,
                fail_gate=False,
                log_message=f"CommonErrorDialog auto-dismissed: {ced_info.message}",
            )
        return CedHandlerResult(
            button_index=-1,
            dismissed=False,
            fail_gate=True,
            log_message=f"CommonErrorDialog has no buttons: {ced_info.message}",
        )

    preferred_labels = ("OK", "Yes") if _checksum_dialog(combined) else ("OK",)
    for label in preferred_labels:
        for index, button in enumerate(buttons):
            if button.label.strip().casefold() == label.casefold():
                return CedHandlerResult(
                    button_index=index,
                    dismissed=True,
                    fail_gate=False,
                    log_message=(
                        f"CommonErrorDialog chose button {index!r} ({button.label}): {ced_info.message}"
                    ),
                )

    for index, button in enumerate(buttons):
        if button.is_safe_default or _SAFE_DIALOG_RE.search(button.label):
            return CedHandlerResult(
                button_index=index,
                dismissed=True,
                fail_gate=False,
                log_message=(
                    f"CommonErrorDialog chose button {index!r} ({button.label}): {ced_info.message}"
                ),
            )

    return CedHandlerResult(
        button_index=0,
        dismissed=True,
        fail_gate=False,
        log_message=f"CommonErrorDialog defaulted to button 0: {ced_info.message}",
    )


def ced_info_from_text(text: str, *, title: str = "") -> ICedInfo:
    """Build ``ICedInfo`` from flattened pywinauto window text."""
    return ICedInfo(
        error_id="",
        title=title,
        message=text.strip(),
        buttons=(),
    )


def ced_info_from_window(
    window: Mapping[str, Any],
    *,
    error_id: str = "",
) -> ICedInfo:
    """Build ``ICedInfo`` from a pywinauto ``dump_relevant_windows`` record."""
    title = str(window.get("title") or "")
    children = window.get("children") or []
    buttons = extract_buttons_from_window_children(children)
    message_parts: list[str] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        control_type = str(child.get("control_type") or "")
        label = str(child.get("title") or "").strip()
        if not label:
            continue
        if control_type == "Button":
            continue
        if control_type in {"Text", "Edit"} or control_type:
            message_parts.append(label)
    message = " ".join(message_parts).strip() or title
    return ICedInfo(
        error_id=error_id,
        title=title,
        message=message,
        buttons=buttons,
    )


def extract_buttons_from_window_children(
    children: Sequence[Mapping[str, Any] | Any],
) -> tuple[CedButton, ...]:
    buttons: list[CedButton] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        if str(child.get("control_type") or "") != "Button":
            continue
        label = str(child.get("title") or "").strip().lstrip("_")
        if not label:
            continue
        buttons.append(CedButton(label=label, is_safe_default=_is_safe_button_label(label)))
    return tuple(buttons)


def ced_info_from_dotnet(ced_info_obj: Any) -> ICedInfo:
    """Map a live ``ICedInfo`` COM/pythonnet object into the offline dataclass."""
    error_id = _dotnet_text(ced_info_obj, "ErrorId", "ErrorID", "Id", "ID")
    title = _dotnet_text(ced_info_obj, "Title", "Caption")
    message = _dotnet_text(ced_info_obj, "Message", "Text", "Description")
    raw_buttons = _dotnet_sequence(ced_info_obj, "Buttons", "ButtonLabels")
    buttons: list[CedButton] = []
    for item in raw_buttons:
        if isinstance(item, str):
            label = item.strip()
            if label:
                buttons.append(CedButton(label=label, is_safe_default=_is_safe_button_label(label)))
            continue
        label = _dotnet_text(item, "Label", "Text", "Caption", "Name")
        if label:
            default = bool(_dotnet_attr(item, "IsDefault", "IsSafeDefault", default=False))
            buttons.append(CedButton(label=label, is_safe_default=default or _is_safe_button_label(label)))
    return ICedInfo(
        error_id=error_id,
        title=title,
        message=message or title,
        buttons=tuple(buttons),
    )


def write_button_index(button_index: Any, value: int) -> None:
    """Write ``ref Int32 buttonIndex`` for pythonnet/list/single-value stubs."""
    if isinstance(button_index, MutableSequence):
        if button_index:
            button_index[0] = int(value)
        else:
            button_index.append(int(value))
        return
    try:
        setattr(button_index, "value", int(value))
    except Exception:
        pass


def merge_ced_journal_into_report(
    report: dict[str, Any],
    journal: CommonErrorDialogJournal | None,
) -> dict[str, Any]:
    if journal is None or not journal.entries:
        return report
    merged = dict(report)
    details = dict(merged.get("details") or {})
    details[_CED_DETAIL_KEY] = journal.as_dict()
    merged["details"] = details
    messages = [*(merged.get("messages") or [])]
    for entry in journal.entries:
        log_message = str(entry.get("log_message") or "").strip()
        if log_message:
            messages.append(log_message)
    merged["messages"] = messages
    return merged


def subscribe_common_error_dialog(
    runtime_events: Any,
    handler: CEDNotificationHandler | None = None,
) -> tuple[CEDNotificationHandler, Any | None]:
    """Subscribe ``CommonErrorDialog`` when the runtime exposes event hooks."""
    ced_handler = handler or CEDNotificationHandler()
    subscription = None
    for attr in ("CommonErrorDialog", "common_error_dialog"):
        event = getattr(runtime_events, attr, None)
        if event is None:
            continue
        try:
            if hasattr(event, "append"):
                event.append(ced_handler.invoke)
                subscription = event
                break
            if callable(event):
                runtime_events.__setattr__(attr, ced_handler.invoke)
                subscription = attr
                break
        except Exception:
            continue
    return ced_handler, subscription


def _checksum_dialog(text: str) -> bool:
    lowered = text.casefold()
    return "checksum" in lowered or "recalculate" in lowered


def _is_safe_button_label(label: str) -> bool:
    normalized = label.strip().casefold()
    return normalized in {"ok", "yes", "continue", "close"}


def _dotnet_text(obj: Any, *names: str) -> str:
    for name in names:
        value = _dotnet_attr(obj, name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _dotnet_sequence(obj: Any, *names: str) -> list[Any]:
    for name in names:
        value = _dotnet_attr(obj, name)
        if value is None:
            continue
        try:
            return list(value)
        except TypeError:
            continue
    return []


def _dotnet_attr(obj: Any, name: str, *aliases: str, default: Any = None) -> Any:
    for candidate in (name, *aliases):
        try:
            return getattr(obj, candidate)
        except Exception:
            continue
    return default
