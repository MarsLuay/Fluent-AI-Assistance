# Logic-level performance markers

Instrumented processing paths can emit opt-in stage timings without comparing
Python execution against Rust execution. Each marker names a logical operation
(archive validation, command classification, component inference, and so on);
it remains the same marker if that operation's implementation changes.

Markers are off by default. Enable them for one process with:

```powershell
$env:TECAN_PERFORMANCE_MARKERS = "1"
# Invoke your usual Python command unchanged.
```

Or on POSIX shells:

```sh
export TECAN_PERFORMANCE_MARKERS=1
# Invoke your usual Python command unchanged.
```

Each completed stage writes one `PERF `-prefixed JSON record to stderr, for
example:

```text
PERF {"duration_ms":12.345,"metrics":{"script_count":8},"name":"patterns.load_indexed_scripts","outcome":"ok","type":"performance_marker"}
```

The records contain stage names, elapsed milliseconds, and bounded aggregate
counts only. They do not include source paths, file contents, or exception text.
Context-managed failed stages are marked with `outcome: "error"` while the
original exception is re-raised; manually delimited spans emit when they finish.
Markers have a small dispatch cost while disabled; enabling them
adds timer and stderr I/O overhead, so use them to locate expensive stages and
then confirm optimizations with repeated, matched runs. Some markers are nested
(for example, project-index archive ingestion contains ZEIA ingestion stages);
compare the nested durations individually and do not sum them as independent
costs.

## Instrumented stages

- **ZEIA ingestion and indexing:** archive inventory/format detection, member
  inspection and normalization, completeness accounting, model assembly,
  project-index input discovery, schema setup, per-archive ingestion and SQLite
  writes, commit, and summary queries.
- **Pattern mining:** indexed-script loading, command classification, combined
  window discovery/persistence, SQLite commit, and summary queries. The
  discovery/persistence marker intentionally covers both together because the Python fallback
  interleaves candidate generation and inserts.
- **XSCR parsing:** XML-tree parsing, step-field indexing, and IR-model
  construction.
- **Catalog indexing:** install and source fingerprinting, incremental-cache
  check, component parse/inference/upsert, workspace parsing/upsert, site path
  indexing, connector reference resolution, liquid-class parse/upsert,
  stale-source cleanup/commit, and final count queries.
- **Worktable geometry:** manifest object selection, component/site/connector
  parsing, workspace resolution, and derived geometry/output shaping.
- **Rust worklist CLI:** CSV loading, transfer validation, GWL record assembly
  and writing, GWL parsing and summary, and worklist semantic analysis.

These are stage timings, not proof that a particular backend was selected and
not guarantees of performance on production exports or installations. Use
representative data only where its use is authorized.
