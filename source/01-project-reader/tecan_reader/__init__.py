"""Local Tecan file readers."""

from .archive import inspect_archive
from .compare import compare_archives
from .gwl import inspect_gwl
from .pattern_library import mine_script_patterns, search_script_patterns, summarize_script_patterns
from .project_index import build_project_index, search_project_index, summarize_project_index
from .script import inspect_xscr
from .xmlobj import inspect_xml_object

__all__ = [
    "build_project_index",
    "compare_archives",
    "inspect_archive",
    "inspect_gwl",
    "inspect_xml_object",
    "inspect_xscr",
    "mine_script_patterns",
    "search_project_index",
    "search_script_patterns",
    "summarize_project_index",
    "summarize_script_patterns",
]
