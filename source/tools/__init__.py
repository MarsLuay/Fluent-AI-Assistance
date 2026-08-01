"""Packaged repository-level Tecan support tools.

Subpackages:
- ``common`` — shared helpers
- ``simulator`` — mesh/texture/registry/launcher
- ``api_v2`` — API V2 mining workflow
- ``connectors`` — connector capability extraction
- ``registry`` — command-registry provenance
- ``prompt`` — prompt builder

Legacy ``from tecan_tools import <module>`` imports still resolve via ``__getattr__``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "api_v2",
    "assign_api_v2_agents",
    "build_api_v2_retry_queue",
    "build_connector_graph",
    "build_fluent_registry",
    "build_procedural_fallback_specs",
    "common",
    "connector_capability_extractor",
    "connectors",
    "diagnose_model_coverage",
    "enrich_command_registry_provenance",
    "extract_api_v2_improvements",
    "extract_fluent_meshes",
    "extract_fluent_textures",
    "launch_api_v2_implementation_agents",
    "launch_simulator",
    "merge_fluent_mesh_libraries",
    "prompt",
    "registry",
    "simulator",
    "tecan_prompt_builder",
    "tecan_prompt_builder_app",
    "terminal_progress",
]

_LEGACY_MODULES: dict[str, str] = {
    "terminal_progress": "tecan_tools.common.terminal_progress",
    "extract_fluent_meshes": "tecan_tools.simulator.extract_fluent_meshes",
    "extract_fluent_textures": "tecan_tools.simulator.extract_fluent_textures",
    "merge_fluent_mesh_libraries": "tecan_tools.simulator.merge_fluent_mesh_libraries",
    "build_fluent_registry": "tecan_tools.simulator.build_fluent_registry",
    "build_connector_graph": "tecan_tools.simulator.build_connector_graph",
    "build_procedural_fallback_specs": "tecan_tools.simulator.build_procedural_fallback_specs",
    "diagnose_model_coverage": "tecan_tools.simulator.diagnose_model_coverage",
    "launch_simulator": "tecan_tools.simulator.launch_simulator",
    "assign_api_v2_agents": "tecan_tools.api_v2.assign_api_v2_agents",
    "build_api_v2_retry_queue": "tecan_tools.api_v2.build_api_v2_retry_queue",
    "extract_api_v2_improvements": "tecan_tools.api_v2.extract_api_v2_improvements",
    "launch_api_v2_implementation_agents": "tecan_tools.api_v2.launch_api_v2_implementation_agents",
    "connector_capability_extractor": "tecan_tools.connectors.connector_capability_extractor",
    "enrich_command_registry_provenance": "tecan_tools.registry.enrich_command_registry_provenance",
    "tecan_prompt_builder": "tecan_tools.prompt.tecan_prompt_builder",
    "tecan_prompt_builder_app": "tecan_tools.prompt.tecan_prompt_builder_app",
}


def __getattr__(name: str) -> Any:
    if name in _LEGACY_MODULES:
        return import_module(_LEGACY_MODULES[name])
    if name in {"common", "simulator", "api_v2", "connectors", "registry", "prompt"}:
        return import_module(f"tecan_tools.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
