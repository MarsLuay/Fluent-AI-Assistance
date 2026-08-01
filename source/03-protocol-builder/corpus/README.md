# Local Protocol Corpus

This directory keeps source-controlled corpus fixtures for protocol-builder QA.
Paths in `local_corpus_manifest.json` are relative to
`source/03-protocol-builder/`.

- `local_corpus_manifest.json` inventories the current source-controlled GWL
  fixtures and reviewed alias-map expectations.
- `gwl/` stores safe fixture copies of local worklist-builder outputs so GWL QA
  does not depend on ignored `build/` folders.

When importing more ZEIA archives, keep their generated project artifacts in
`ready-to-import/<project>/temp_files/`. Copy only reviewed, safe, permanent
fixtures here before adding them to the manifest, then expand alias maps from
the reviewed import candidates.
