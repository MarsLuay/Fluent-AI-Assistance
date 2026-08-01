from __future__ import annotations

from fluent_pipeline.generation_options import GenerationOptions
from fluent_pipeline.request_factory import merge_generation_options_from_spec


def test_spec_generation_option_overrides_preserve_runtime_fluent_values() -> None:
    spec = {"generation": {"simulate": True, "compile_xscr": True}}

    options = merge_generation_options_from_spec(
        spec,
        GenerationOptions(
            fluent_method="Local Fluent Method",
            fluent_host="fluent-host.example",
            fluent_port=50099,
            fluent_insecure=True,
        ),
    )

    assert options.fluent_method == "Local Fluent Method"
    assert options.fluent_host == "fluent-host.example"
    assert options.fluent_port == 50099
    assert options.fluent_insecure is True
