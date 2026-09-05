from dataclasses import dataclass
from typing import Optional, Union, Any

@dataclass
class SimulationOptions:
    fail_on_opaque: bool = False
    min_coverage: Optional[float] = None
    strict: bool = False
    subroutine_registry: Any = None
    record_snapshots: Union[bool, str] = True
    snapshot_mode: Optional[str] = None
