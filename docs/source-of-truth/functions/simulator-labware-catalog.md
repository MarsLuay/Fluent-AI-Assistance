# Functions: simulator-labware-catalog

Source roots: `source/04-protocol-simulator/src/data/labwareCatalog.ts`

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `hardwareProfileFromZeia` | `labwareCatalog.ts` | `(input: {functionalGroup?, nameText?, shape?}) -> HardwareProfile` | FG→profile; exact tube phrases | none |
| `registerLabwareCatalogEntries` | same | `(entries) -> number` | Register ZEIA rows | mutates module registry |
| `registerLabwareCatalogFromDefinitions` | same | `(…) -> number` | From definition models | mutates |
| `registerLabwareCatalogPayload` | same | `(payload) -> number` | From JSON payload | mutates |
| `hasZeiaLabwareCatalog` | same | `() -> boolean` | Any ZEIA rows? | none |
| `resolveLabwareGeometry` | same | `(catalogName, label, meshGuid?, meshName?) -> ResolvedLabwareGeometry` | Dims/mesh resolve | none |
| `inferCatalogNameFromLabel` | same | `(label) -> string` | Label→catalog name hint | none |
| `textIncludesExactTubePhrase` | same (private) | `(text) -> boolean` | `tube holder` / `tube runner` only | none |
