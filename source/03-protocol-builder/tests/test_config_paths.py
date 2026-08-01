from __future__ import annotations

import os
from pathlib import Path

from fluent_pipeline import config


def test_default_fluentcoder_python_uses_repo_level_venv() -> None:
    expected = config.TECAN_AI_DIR / ".venv"
    expected_python = expected / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    assert config.DEFAULT_FLUENTCODER_PYTHON == expected_python
    assert config.fluentcoder_python() == expected_python
    assert config.DEFAULT_FLUENTCODER_PYTHON.parent.parent == expected
    assert config.DEFAULT_FLUENTCODER_PYTHON.parent.parent != config.PROJECT_DIR / ".venv"
