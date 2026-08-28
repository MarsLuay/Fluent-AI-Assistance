"""FluentControl protocol-variable tokens for compile-time field references."""

import re
from dataclasses import dataclass

FC_VAR_PREFIX = "@fc:"

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_fc_variable_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    candidate = name.strip()
    if not candidate:
        return False
    return bool(_VARIABLE_NAME_RE.fullmatch(candidate))


@dataclass(frozen=True)
class FCVariableToken:
    """Reference to a FluentControl script variable (for example ``PlateType``)."""

    name: str

    def __str__(self) -> str:
        return self.name


def encode_fc_variable(name: str) -> str:
    """Encode an FC variable reference for IR ``labware_type`` fields."""
    return f"{FC_VAR_PREFIX}{name}"


def decode_fc_variable(value: str) -> str | None:
    """Return the FC variable name when ``value`` is an encoded IR reference."""
    if not isinstance(value, str) or not value.startswith(FC_VAR_PREFIX):
        return None
    name = value[len(FC_VAR_PREFIX) :]
    return name if name else None


def as_labware_type(value: str | FCVariableToken) -> str:
    """Normalize a catalog name or FC variable token for IR storage."""
    if isinstance(value, FCVariableToken):
        return encode_fc_variable(value.name)
    return value
