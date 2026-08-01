# Functions: fluentcoder-catalog-xlqc

Source roots: `libs/fluentcoder/fluentcoder/catalog/`

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `load_xlqc` | `xlqc.py` | `(path) -> XlqcLiquidClass` | Metadata parse (guid/name/heads) | LRU-cached file read |
| `_load_xlqc_cached` | `xlqc.py` | `(path_str) -> XlqcLiquidClass` | Cache body | parse errors propagate |
| `resolve_liquid_class_by_name` | `catalog.py` | `(name, *, db_path=None) -> LiquidClassEntry \| None` | Exact SQL name match | opens index |
| `index_exists` | `catalog.py` | `(…) -> bool` | Index present? | none |
| `open_index` | `catalog.py` | context manager | SQLite connection | I/O |
| indexer glob `*.xlqc` | `indexer.py` | (build_index path) | Insert liquid_classes rows via `load_xlqc` | writes DB |
