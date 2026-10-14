"""Per-stage latency and tokens from server traces (ADR 002, SPEC §8): no network."""

from __future__ import annotations

import json
from pathlib import Path

from evals.stages import percentile, read_traces, summarize


def trace(rid: str, stages: dict[str, float], usage: list[tuple[str, int, int]], **extra: object):
    return {
        "event": "ask",
        "request_id": rid,
        "stages_ms": stages,
        "usage": [
            {"stage": s, "attempt": n + 1, "input_tokens": i, "output_tokens": o}
            for n, (s, i, o) in enumerate(usage)
        ],
        "status": 200,
        **extra,
    }


def run(rid: str, outcome: str) -> dict:
    return {"outcome": outcome, "response": {"meta": {"request_id": rid}}}


REPORT = {
    "provenance": {"code": {"value": "abc"}, "set": {"name": "golden"}},
    "cases": [
        {"id": "a", "runs": [run("r1", "answer"), run("r2", "answer")]},
        {"id": "s", "runs": [run("r3", "safety")]},
        {"id": "e", "runs": [{"outcome": "error", "response": {"error": {"request_id": "r4"}}}]},
    ],
}
TRACES = {
    "r1": trace(
        "r1",
        {"extract": 400, "retrieve": 2, "generate": 2000},
        [("extract", 300, 40), ("generate", 2500, 200)],
        latency_ms=2410,
    ),
    "r2": trace(
        "r2",
        {"extract": 600, "retrieve": 4, "generate": 3000},
        # a rejected answer was billed too: two generate attempts
        [("extract", 310, 50), ("generate", 2400, 180), ("generate", 2400, 190)],
        latency_ms=3610,
        invalid_output=["generate#1: guarantee wording in a must_exclude answer: 'safe'"],
    ),
    "r3": trace("r3", {"extract": 500, "retrieve": 1}, [("extract", 290, 45)], latency_ms=505),
}


def test_log_lines_that_are_not_ask_traces_are_ignored(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    lines = [
        "INFO:     Uvicorn running on http://127.0.0.1:8765",
        json.dumps(TRACES["r1"]),
        '{"event": "startup"}',
        "{not json",
        json.dumps(TRACES["r3"]),
    ]
    log.write_text("\n".join(lines) + "\n")
    assert set(read_traces(log)) == {"r1", "r3"}


def test_stages_are_summarized_apart_for_answers_and_refusals() -> None:
    result = summarize(REPORT, TRACES)

    answers, refusals = result["answers"], result["refusals"]
    assert answers["requests"] == 2
    assert answers["latency_ms"]["extract"] == {"median": 500, "p95": 600}
    assert answers["latency_ms"]["generate"] == {"median": 2500, "p95": 3000}
    assert answers["latency_ms"]["total"] == {"median": 3010, "p95": 3610}
    # tokens are per request, summed over attempts
    assert answers["tokens"]["generate"] == {
        "input": {"median": 3650, "p95": 4800},
        "output": {"median": 285, "p95": 370},
    }
    assert answers["invalid_output"] == 1
    assert refusals["requests"] == 1
    assert refusals["by_reason"] == {"safety": 1}
    assert "generate" not in refusals["latency_ms"]
    assert refusals["latency_ms"]["retrieve"] == {"median": 1, "p95": 1}


def test_runs_without_a_trace_and_technical_errors_are_counted_not_guessed() -> None:
    result = summarize(REPORT, {k: v for k, v in TRACES.items() if k != "r2"})
    assert result["answers"]["requests"] == 1
    assert result["unmatched_runs"] == 1
    assert result["technical_errors"] == 1


def test_p95_is_nearest_rank() -> None:
    values = list(range(1, 21))  # 1..20
    assert percentile(values, 95) == 19
    assert percentile([7], 95) == 7
