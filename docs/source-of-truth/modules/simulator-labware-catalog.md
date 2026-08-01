# Module: simulator-labware-catalog

**Paths:** `source/04-protocol-simulator/src/data/labwareCatalog.ts` (callers in `parsers.ts`, `fluentGeometry.ts`)  
**Purpose:** Register ZEIA labware dims/meshes; map FunctionalGroup → `HardwareProfile`.  
**Public surface:** `hardwareProfileFromZeia`, `registerLabwareCatalog*`, `resolveLabwareGeometry`, geometry helpers  
**Depends on:** imported catalog JSON / definitions  
**Invariants:** No filter/DWP/tip/adapter/falcon keyword invent; tube holder/runner = exact phrases only.  
**Related functions:** [functions/simulator-labware-catalog.md](../functions/simulator-labware-catalog.md)  
**Related types:** [types/simulator-labware-catalog.md](../types/simulator-labware-catalog.md)
