# ZEIA compatibility matrix

This matrix is a synthetic, public compatibility contract for the structural
ZEIA adapters. It does not claim a particular FluentControl release. The
`export_version` fields record only schema evidence present in each fixture.

Each `*.zeia.json` file is a deterministic recipe, not a materialized archive.
The test helper creates temporary ZIP/ZEIA files with fixed timestamps. The
representative recipes reuse the existing synthetic full-export source files
and add Unicode, additive metadata, and a custom asset without importing lab
content.

`compatibility_manifest.json` is the CI-readable source of matrix coverage.
Update its fixture checksums and add detection, golden, and negative-neighbor
coverage whenever a supported adapter or export family changes.
