# Contributing

fluentcoder is currently open for **technical feedback**, not general code
contributions or production support commitments.

Please use issues or discussion threads to report:

- API design feedback from real FluentControl or lab automation workflows.
- Simulator checks that are wrong, too strict, or missing.
- Documentation gaps that make the project hard to evaluate.
- Inaccuracies in [NOTICE.md](NOTICE.md), [REVIEW_NOTES.md](REVIEW_NOTES.md), or
  [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md).

## Authoring loop

fluentcoder ships two authoring paths:

| Path | Dependencies | CLI |
|---|---|---|
| **Hand authoring** | Core only (`pydantic`, `PyYAML`) | `compile`, `simulate`, `decompile`, `catalog` |
| **LM authoring loop** | Optional `.[authoring]` (`langchain-core`, `langchain-openai`, `langgraph`) + model endpoint / API key | `author`, `chat`, `deploy` |

The LM loop lives under `fluentcoder/authoring/` (LangGraph state machine,
tool registry, optional FluentControl shell validation). Install it with:

```bash
pip install -e ".[authoring,dev]"
```

See [docs/authoring.md](docs/authoring.md) § Chat-driven authoring for
`PromptAuthoringSession`, tracing env vars, and validation behavior.

### protocol-builder policy

The Fluent AI-Assistance **protocol-builder** pipeline (`source/03-protocol-builder`)
imports fluentcoder for IR, compile, and simulate. It **does not** run
`fluentcoder author`, `fluentcoder chat`, or `fluentcoder deploy` — by design,
documented in `source/03-protocol-builder/AGENTS.md`. That keeps the default
Codex/offline workflow free of API keys, LM endpoints, and automated datastore
deploys.

If you are evaluating fluentcoder inside protocol-builder, use hand-authored
Python or pipeline-generated IR — not the LM CLI entry points.

## Before suggesting instrument use

Read [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) § Gates before robot
use. Simulate and compile are design-time tools; hardware runs need FluentControl
and lab sign-off.
