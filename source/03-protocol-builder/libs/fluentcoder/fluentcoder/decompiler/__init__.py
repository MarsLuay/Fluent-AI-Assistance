"""Decompiler — turn an .xscr back into a fluentcoder Python protocol.

The reverse of the renderer: parse FluentControl XML into a Pydantic
``Protocol`` IR (xscr_parser), then emit a self-contained Python source
file with a ``build_worktable()`` factory that, when executed, re-emits
the same .xscr (codegen).
"""

from .xscr_parser import parse_xscr
from .codegen import emit_python
from .corpus import (
    CorpusConfig,
    CorpusResult,
    aggregate_unsupported_command_ids,
    count_generic_step_types,
    discover_subroutine_dirs,
    default_ready_to_import_root,
    render_corpus_report_markdown,
    resolve_xscr_paths,
    run_corpus_report,
    run_decompiled_corpus,
    suggest_parser_priorities,
    summarize_corpus_results,
)

__all__ = [
    "parse_xscr",
    "emit_python",
    "CorpusConfig",
    "CorpusResult",
    "aggregate_unsupported_command_ids",
    "count_generic_step_types",
    "discover_subroutine_dirs",
    "default_ready_to_import_root",
    "render_corpus_report_markdown",
    "resolve_xscr_paths",
    "run_corpus_report",
    "run_decompiled_corpus",
    "suggest_parser_priorities",
    "summarize_corpus_results",
]
