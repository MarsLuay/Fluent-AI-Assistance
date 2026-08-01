"""Tests for workflow event timing helpers."""

from __future__ import annotations

import json

from fluent_pipeline.workflow_events import (
    configure_workflow_sinks,
    reset_workflow_sinks,
    timing_summary,
    workflow_phase,
)


def test_workflow_phase_records_duration() -> None:
    events: list[dict] = []
    configure_workflow_sinks(event_sink=events.append, workflow_start=0.0)
    try:
        with workflow_phase("validate_ready_gates", "gate example", gate="zeia"):
            pass
        summary = timing_summary()
    finally:
        reset_workflow_sinks()

    assert len(events) == 2
    assert events[0]["status"] == "start"
    assert events[0]["gate"] == "zeia"
    assert events[1]["status"] == "done"
    assert int(events[1]["duration_ms"]) >= 0

    assert summary["phase_count"] == 1
    assert summary["phases"][0]["stage"] == "validate_ready_gates"


def test_timing_summary_serializes() -> None:
    events: list[dict] = []
    configure_workflow_sinks(event_sink=events.append, workflow_start=0.0)
    try:
        with workflow_phase("process_media", "resolve"):
            pass
    finally:
        reset_workflow_sinks()
    payload = timing_summary()
    json.dumps(payload)
