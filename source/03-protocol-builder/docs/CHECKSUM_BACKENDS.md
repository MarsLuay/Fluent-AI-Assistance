# FluentControl checksum backends

The protocol builder recomputes edited datastore entry `<Checksum>` values only
through a verified backend. Checksum recompute is **ON BY DEFAULT**: a vendored
pure-Python implementation of the FluentControl checksum algorithm ships in
`fluent_pipeline/checksum.py`, so the offline pipeline stamps correct checksums
and Gate 23 passes import-clean **without** `--waive-checksum-recompute` and
without a FluentControl machine. The waiver is only needed in the genuine
no-backend case (the vendored backend's self-verification failed); in that case
packaging keeps edited checksums blank and Gate 23 blocks unless the waiver is
set.

## Discovery order

1. `TECAN_CHECKSUM_BACKEND`, when set.
2. `fluentcoder.catalog.fc_install`, when it exposes a working
   `shared_core()` / `rewrite_checksum_in_place(...)` path.
3. Direct `fluentcontrol_core` import.
4. **Vendored pure-Python backend** (`fluent_pipeline.checksum`). This is the
   always-available default offline path. It implements the FluentControl
   algorithm directly and self-verifies against small known-good datastore
   fixtures embedded in `fluent_pipeline/_checksum_fixtures.py`, so it does not
   need the large extracted sample trees to be present.

> The older empirical/brute-force backend — which rediscovered the algorithm at
> runtime by hashing the large extracted sample trees (~290 s, fragile, and
> absent in a clean checkout) — has been **retired**. The vendored pure-Python
> backend, whose algorithm was independently confirmed byte-exact against 41,763
> known-good datastore entries (both the MD5 `VxData` branch and the SHA-256
> metadata-root branch, 0 mismatches), fully supersedes it.

Every discovered backend must pass a self-test before it is used. The vendored
backend self-verifies against its embedded fixtures; the real-bridge tiers, when
real samples are available, blank a sample `<Checksum>`, recompute, and require
byte-exact equality with the original file (falling back to a synthetic probe
when no samples are present).

## Vendored algorithm

Reverse-engineered and confirmed byte-for-byte against known-good datastore
entries exported from a real FluentControl system. The original derivation
matched 3047/3047 distinct entries; a later independent re-verification over
every locally available extracted datastore entry matched 41,763/41,763 real
known-good entries (0 mismatches) across both branches:

- **`VxData` datastore objects** (`.xscr`, `.xml`, `.xwsp`, `.xcmp`, `.xlqc`,
  `.xcon`; 32-character checksum): uppercase-hex `MD5` over the *inner* content
  of the `<Payload>` element after blanking `<Checksum>`, collapsing every
  `>`-whitespace-`<` run to `><`, and stripping the result.
- **Archive-metadata roots** (`ArchiveContent`, `DirectoryMappings`,
  `NodeDescription`, `SystemInfo`; 64-character checksum): uppercase-hex
  `SHA-256` over the *entire* `<Payload>` element with the same blanking,
  inter-tag whitespace collapse, and strip.

Whitespace inside text nodes is preserved; only inter-tag whitespace is
collapsed. The entry bytes (including the UTF-8 BOM and CRLF newlines as stored)
are hashed directly with no other normalization.

If `fluent_pipeline.checksum.verify_self()` ever fails (e.g. fixture
corruption), the backend refuses to activate and the pipeline falls back to the
safe blank-checksum behaviour rather than shipping a guessed value.

## `TECAN_CHECKSUM_BACKEND`

Supported values:

- `none`: disable checksum recomputation (forces the blank-checksum +
  Gate-23-block path).
- `fluentcoder` or `fc_install`: use the bundled FluentControl bridge discovery.
- `module:pkg.mod` or `pkg.mod`: import a Python module.
- `path/to/backend.py`: import a Python file.
- `shim:path/to/exe`: run a process that reads entry bytes from stdin and writes
  checksummed bytes to stdout.
- `clr`: use a site-local pythonnet adapter configured by
  `TECAN_CHECKSUM_CLR_ASSEMBLY`, `TECAN_CHECKSUM_CLR_TYPE`, and
  `TECAN_CHECKSUM_CLR_METHOD`.

Python module/file backends may expose any one of:

- `recompute_checksum_bytes(data: bytes) -> bytes | None`
- `rewrite_checksum_in_place(path: pathlib.Path) -> bool`
- `shared_core()` returning an object with `rewrite_checksum(path, in_place=True)`

The backend must preserve the original entry byte shape. The protocol builder
does not reserialize XML; it accepts only backend output that still contains a
non-blank `<Checksum>`.

