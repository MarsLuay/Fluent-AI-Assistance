"""Offline stub: live FluentControl ``ICommand.ToXML()`` comparison is not supported."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import ICommand


def native_to_xml_available() -> bool:
    return False


class NativeToXmlProvider:
    name = "offline_serializer"
    bootstrap_error = ""

    @property
    def available(self) -> bool:
        return False

    def to_xml(self, command: ICommand) -> str:
        return ""
