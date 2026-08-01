# Functions: fluent-pipeline-liquid-classes

Source roots: `fluent_pipeline/liquid_classes_export.py`

| Symbol | File | Signature | Purpose | Side effects / errors |
| --- | --- | --- | --- | --- |
| `LIQUID_CLASSES_SCHEMA_VERSION` | `liquid_classes_export.py` | const | module constant | , |
| `LIQUID_CLASSES_FILENAME` | `liquid_classes_export.py` | const | module constant | , |
| `INSTANCE_SUFFIX_RE` | `liquid_classes_export.py` | const | module constant | , |
| `_LIQUID_CLASSES_REL` | `liquid_classes_export.py` | const | module constant | , |
| `_EQ_SET_LOCAL` | `liquid_classes_export.py` | const | module constant | , |
| `_DPS_SET_LOCAL` | `liquid_classes_export.py` | const | module constant | , |
| `_SUBCLASS_LOCAL` | `liquid_classes_export.py` | const | module constant | , |
| `strip_fluent_instance_suffix` | `fluent_naming.py` (re-exported from `liquid_classes_export.py`) | `(value)` | Strip Fluent instance suffixes such as ``[001]`` / ``[platecount]``. | see source |
| `_SECTION_KINDS` | `liquid_classes_export.py` | const | module constant | , |
| `_EQ_FIELD_ALIASES` | `liquid_classes_export.py` | const | module constant | , |
| `_DPS_FIELD_ALIASES` | `liquid_classes_export.py` | const | module constant | , |
| `_FIELD_ALIASES` | `liquid_classes_export.py` | const | module constant | , |
| `parse_xlqc` | `liquid_classes_export.py` | `(path)` | Parse one ``.xlqc`` into portable catalog fields.  Filename stem is the LiquidClass GUID referenced by ``.xscr`` (same r | see source |
| `build_liquid_classes_catalog` | `liquid_classes_export.py` | `()` | Mine liquid classes from manifest ``.xlqc`` objects and/or DataStore walk. | see source |
| `write_liquid_classes_catalog` | `liquid_classes_export.py` | `(destination)` | Write ``liquid_classes.json`` when at least one ``.xlqc`` entry exists. | see source |
| `write_liquid_classes_for_context` | `liquid_classes_export.py` | `(context_root, manifest)` | Write catalog next to ``manifest.json`` under a project context root. | see source |
| `load_liquid_classes_catalog` | `liquid_classes_export.py` | `(path)` | see source | see source |
| `alias_maps_from_liquid_classes_catalog` | `liquid_classes_export.py` | `(catalog)` | Derive liquid_class alias map (instance label → type name) from the catalog. | see source |
| `resolve_liquid_class_guid` | `liquid_classes_export.py` | `(name, catalog)` | Exact name → GUID from mined catalog (no generation.yaml invent). | see source |
| `resolve_liquid_classes_dir` | `liquid_classes_export.py` | `(path)` | see source | see source |
| `discover_liquid_classes_dir` | `liquid_classes_export.py` | `(context_root)` | see source | see source |
| `_xlqc_paths_from_manifest (priv)` | `liquid_classes_export.py` | `(manifest)` | see source | see source |
| `_catalog_entry (priv)` | `liquid_classes_export.py` | `(item)` | see source | see source |
| `_mine_profiles (priv)` | `liquid_classes_export.py` | `(root)` | Mine head×tip profiles from typed Fluent liquid-class sets. | see source |
| `_apply_eq (priv)` | `liquid_classes_export.py` | `(profile, asp, disp, mix)` | see source | see source |
| `_profile_bucket (priv)` | `liquid_classes_export.py` | `(buckets, head, tip)` | see source | see source |
| `_summary_sections (priv)` | `liquid_classes_export.py` | `(profiles)` | Flatten first-seen profile params into top-level aspirate/dispense/mix. | see source |
| `_clean_detection (priv)` | `liquid_classes_export.py` | `(detection)` | see source | see source |
| `_set_context (priv)` | `liquid_classes_export.py` | `(elem)` | see source | see source |
| `_direct_or_nested_text (priv)` | `liquid_classes_export.py` | `(elem, local_name)` | Prefer direct child text; else first matching descendant text. | see source |
| `_equation_set_fields (priv)` | `liquid_classes_export.py` | `(elem)` | see source | see source |
| `_detection_set_fields (priv)` | `liquid_classes_export.py` | `(elem)` | see source | see source |
| `_microscript_section_names (priv)` | `liquid_classes_export.py` | `(elem)` | see source | see source |
| `_mine_microscript (priv)` | `liquid_classes_export.py` | `(elem)` | Mine MicroScriptSection bodies as ordered command-type sequences.  Stores Object ``Type`` leaf names only (not full micr | see source |
| `_walk_script_objects (priv)` | `liquid_classes_export.py` | `(objects_elem, out)` | Append Object Type leaf names; return True if truncated by max_commands. | see source |
| `_merge_microscript (priv)` | `liquid_classes_export.py` | `(left, right)` | see source | see source |
| `_snake_case (priv)` | `liquid_classes_export.py` | `(value)` | see source | see source |
| `_pipetting_sections (priv)` | `liquid_classes_export.py` | `(root)` | Legacy fallback: aspirate/dispense/mix scalars under matching ancestors. | see source |
| `_section_fields (priv)` | `liquid_classes_export.py` | `(section)` | see source | see source |
| `_merge_section_fields (priv)` | `liquid_classes_export.py` | `(left, right)` | see source | see source |
| `_coerce_scalar (priv)` | `liquid_classes_export.py` | `(text)` | see source | see source |
| `_find (priv)` | `liquid_classes_export.py` | `(elem, local_name)` | see source | see source |
| `_local_name (priv)` | `liquid_classes_export.py` | `(tag)` | see source | see source |
| `_text (priv)` | `liquid_classes_export.py` | `(elem)` | see source | see source |
| `_child_text (priv)` | `liquid_classes_export.py` | `(elem, local_name)` | see source | see source |
| `_norm (priv)` | `liquid_classes_export.py` | `(value)` | see source | see source |
| `_clean (priv)` | `liquid_classes_export.py` | `(payload)` | see source | see source |
