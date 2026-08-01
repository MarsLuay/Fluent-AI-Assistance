# Desktop automation wheelhouse

This folder is the offline cache for Windows desktop automation dependencies
used to drive FluentControl during local validation.

Primary stack:

- `pywinauto` with the UI Automation (`uia`) backend for control-tree actions.
- `comtypes` for UI Automation COM access.
- `pyautogui` for coordinate/hotkey fallback when an app hides controls.
- `mss` and `pillow` for screenshots and visual diagnostics.
- `pyperclip` for reliable text paste.
- `psutil` for process discovery and lifecycle checks.

`python -m fluent_pipeline.bootstrap` reads `requirements.txt` together with
`constraints.txt`, downloads wheels into `wheels/` when network is available,
and installs from `wheels/` with `--no-index` on later offline runs.

To pre-populate the cache manually:

```powershell
.\.venv\Scripts\python.exe -m pip wheel --wheel-dir vendor\desktop-automation\wheels -c vendor\desktop-automation\constraints.txt -r vendor\desktop-automation\requirements.txt
```
