from dataclasses import dataclass
from typing import Mapping, Optional, Union, Any

@dataclass
class SimulationOptions:
    fail_on_opaque: bool = False
    min_coverage: Optional[float] = None
    strict: bool = False
    subroutine_registry: Any = None
    record_snapshots: Union[bool, str] = True
    snapshot_mode: Optional[str] = None
    # Test-only/offline injected outcomes keyed by macro name. Values may be a
    # single outcome or a list consumed once per invocation.
    driver_outcomes: Optional[Mapping[str, Any]] = None
