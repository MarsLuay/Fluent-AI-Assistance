# Types: fluentcoder-catalog-xlqc

| Symbol | File | Fields / notes |
| --- | --- | --- |
| `XlqcLiquidClass` | `catalog/xlqc.py` | frozen dataclass: `guid`, `name`, `head`, `file_path`, `supported_heads` |
| `LiquidClassEntry` | `catalog/catalog.py` | SQL row wrapper for liquid_classes table |
| SQL `liquid_classes` | `catalog.py` DDL | install_key, guid, name, file_path, supported_heads, … |
