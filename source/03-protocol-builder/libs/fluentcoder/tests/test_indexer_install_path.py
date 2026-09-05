from __future__ import annotations
from pathlib import Path
from fluentcoder.catalog.indexer import install_path_default, DEFAULT_INSTALL_PATH

def test_install_path_default_no_env_var(monkeypatch):
    monkeypatch.delenv("FLUENTCODER_FC_INSTALL", raising=False)
    assert install_path_default() == DEFAULT_INSTALL_PATH

def test_install_path_default_with_env_var(monkeypatch):
    custom_path = r"D:\Custom\Path"
    monkeypatch.setenv("FLUENTCODER_FC_INSTALL", custom_path)
    assert install_path_default() == Path(custom_path)
