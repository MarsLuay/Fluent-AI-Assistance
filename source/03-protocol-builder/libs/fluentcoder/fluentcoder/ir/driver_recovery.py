"""Source-backed driver error/recovery policy helpers.

FluentControl driver error handling is not a stable command contract in the
repository's installed reference catalog.  This module therefore keeps the
normalized fields deliberately small and retains the source XML for any
vendor-specific policy.  Unknown actions are preserved, but never simulated.
"""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import xml_compat as ET


_RECOVERY_HINTS = frozenset(
    {
        "error",
        "exception",
        "failure",
        "recover",
        "recovery",
        "retry",
        "handler",
        "onerror",
        "abort",
        "stop",
        "continue",
        "operator",
        "prompt",
    }
)
_ACTIONS = frozenset({"retry", "abort", "stop", "continue", "operator", "handler"})
_FIELD_TAGS = {
    "error_category": "ErrorCategory",
    "error_code": "ErrorCode",
    "error_source": "ErrorSource",
    "action": "Action",
    "max_retries": "MaxRetries",
    "handler_target": "HandlerTarget",
}


class DriverRecoveryPolicy(BaseModel):
    """Normalized, source-preserving driver failure policy.

    ``raw_xml`` is authoritative for imported policies.  ``element_name``
    allows a caller with an explicitly verified XSCR element name to author a
    policy without hand-editing XML; the renderer only emits fields that are
    present and uses the stable generic field names for that verified element.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    error_category: str | None = None
    error_code: str | None = None
    error_source: str | None = None
    action: str | None = None
    max_retries: int | None = Field(default=None, ge=0)
    handler_target: str | None = None
    element_name: str | None = None
    source_provenance: str = "unknown"
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_xml: str | None = None
    additional_raw_xml: list[str] = Field(default_factory=list)

    @field_validator("action", "source_provenance", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @property
    def normalized_action(self) -> str | None:
        value = (self.action or "").strip().casefold().replace("_", "-")
        return value or None

    @property
    def is_known_action(self) -> bool:
        return self.normalized_action in _ACTIONS

    def validation_issues(self) -> list[str]:
        """Return conservative offline diagnostics for this policy."""
        issues: list[str] = []
        if not self.action:
            issues.append("driver recovery policy has no action")
        if self.normalized_action == "retry" and self.max_retries is None:
            issues.append("retry recovery policy requires bounded max_retries")
        if self.normalized_action == "handler" and not self.handler_target:
            issues.append("handler recovery policy requires handler_target")
        if not self.is_known_action and not self.raw_xml:
            issues.append("unknown recovery action requires preserved raw_xml")
        if self.source_provenance not in {"source", "user", "unknown"}:
            issues.append("source_provenance must be source, user, or unknown")
        return issues

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

    def to_xml(self) -> str:
        """Render an authored policy or return its exact source element."""
        if self.raw_xml:
            return "\n".join([self.raw_xml, *self.additional_raw_xml])
        if not self.element_name:
            raise ValueError(
                "driver recovery policy cannot be rendered without a verified element_name"
            )
        issues = self.validation_issues()
        if issues:
            raise ValueError("; ".join(issues))
        fields = (
            ("error_category", self.error_category),
            ("error_code", self.error_code),
            ("error_source", self.error_source),
            ("action", self.action),
            ("max_retries", self.max_retries),
            ("handler_target", self.handler_target),
        )
        children = []
        for field_name, value in fields:
            if value is None:
                continue
            tag = _FIELD_TAGS[field_name]
            children.append(f"<{tag}>{escape(str(value))}</{tag}>")
        return f"<{self.element_name}>" + "".join(children) + f"</{self.element_name}>"


def is_recovery_element(element: ET.Element) -> bool:
    local = _local(element.tag).casefold()
    if local in {"executionsettings", "applicationdrivermacro", "legacydrivermacro"}:
        return False
    return any(token in local for token in _RECOVERY_HINTS)


def parse_driver_recovery_policy(macro_element: ET.Element) -> DriverRecoveryPolicy | None:
    """Extract a policy only when the source contains an explicit recovery node."""
    candidates: list[ET.Element] = []
    for child in list(macro_element):
        if isinstance(child.tag, str) and is_recovery_element(child):
            candidates.append(child)
    if not candidates:
        return None
    first = candidates[0]
    values: dict[str, Any] = {
        "element_name": _local(first.tag),
        "source_provenance": "source",
        "raw_xml": ET.tostring(first, encoding="unicode"),
        "additional_raw_xml": [ET.tostring(item, encoding="unicode") for item in candidates[1:]],
        "source_metadata": {
            "element_name": _local(first.tag),
            "attributes": dict(first.attrib),
        },
    }
    for element in first.iter():
        if not isinstance(element.tag, str):
            continue
        text = (element.text or "").strip()
        if not text:
            continue
        key = _local(element.tag).casefold().replace("-", "_")
        if key in {"errorcategory", "category", "errortype", "exceptioncategory"}:
            values.setdefault("error_category", text)
        elif key in {"errorcode", "code", "exceptioncode"}:
            values.setdefault("error_code", text)
        elif key in {"errorsource", "source", "driver", "drivername"}:
            values.setdefault("error_source", text)
        elif key in {"action", "recoveryaction", "onerror", "failureaction"}:
            values.setdefault("action", text)
        elif key in {"maxretries", "retrycount", "retries", "attempts"}:
            try:
                values.setdefault("max_retries", int(text))
            except ValueError:
                values["source_metadata"]["invalid_max_retries"] = text
        elif key in {"handlertarget", "handler", "subroutine", "target", "reference"}:
            values.setdefault("handler_target", text)
    return DriverRecoveryPolicy(**values)


def driver_recovery_from_mapping(value: Any) -> DriverRecoveryPolicy | None:
    if value is None:
        return None
    if isinstance(value, DriverRecoveryPolicy):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("recovery_policy must be a mapping or DriverRecoveryPolicy")
    return DriverRecoveryPolicy(**dict(value))


def validate_driver_recovery_mapping(value: Any) -> list[str]:
    policy = driver_recovery_from_mapping(value)
    return [] if policy is None else policy.validation_issues()


def _local(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


__all__ = [
    "DriverRecoveryPolicy",
    "driver_recovery_from_mapping",
    "is_recovery_element",
    "parse_driver_recovery_policy",
    "validate_driver_recovery_mapping",
]
