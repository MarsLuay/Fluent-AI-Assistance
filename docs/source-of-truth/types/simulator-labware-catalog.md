# Types: simulator-labware-catalog

| Symbol | File | Notes |
| --- | --- | --- |
| `LabwareGeometrySpec` | `labwareCatalog.ts` | dims, aliases, functionalGroup, mesh refs |
| `ResolvedLabwareGeometry` | same | spec + resolution metadata |
| `ZeiaLabwareCatalogEntry` | same | imported ZEIA catalog row shape |
| `HardwareProfile` | `types.ts` (simulator) | tip-box, plate, tube-holder, generic, … |
| `LabwareModelFit` | `labwareCatalog.ts` | `catalog-dimensions` \| `fluent-dimensions` \| `bounding-box` |
