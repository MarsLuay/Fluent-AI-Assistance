# Reviewer Guide

This guide is for reviewers evaluating whether fluentcoder's model fits real
FluentControl workflows.

## Setup

```bash
python -m pip install -e .
python -m pytest tests/ -q
```

Some catalog and workspace tests require a local FluentControl installation.
Without one, those tests should skip or use offline fallbacks.

## First things to inspect

- `examples/simple_transfer.py` for the smallest authoring flow.
- `docs/authoring.md` for the public Python API.
- `docs/simulator.md` for physical invariant checks and snapshots.
- `docs/decompile.md` for `.xscr` to Python recovery.
- `docs/catalog.md` for install-backed labware and workspace lookup.

## Useful feedback

- Whether the authoring API matches how FluentControl users describe work.
- Whether the simulator catches the right classes of mistakes.
- Whether generated protocols should expose more explicit FluentControl
  settings.
- Which command families or protocol patterns should be prioritized next.
- Which documentation claims need more evidence before the project is trusted.

## Safety expectations

Do not run generated `.xscr` output on an instrument without normal lab review,
FluentControl validation, and site-specific safety checks. fluentcoder is not a
replacement for instrument qualification or method validation.

See [RELEASE_READINESS.md](RELEASE_READINESS.md) for the full simulate-vs-hardware
boundary and pre-robot gate checklist.
