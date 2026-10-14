"""Latency and tokens per pipeline stage, from the server's request traces (ADR 002).

    uv run uvicorn recipe_qa.api:app --port 8765 2>&1 | tee evals/reports/server.log
    uv run python evals/run.py --base-url http://127.0.0.1:8765 --set golden --milestone NAME
    uv run python evals/stages.py --report evals/reports/milestones/NAME.json \\
        --log evals/reports/server.log --milestone NAME-stages

The service logs one JSON trace per /ask request (stages_ms, usage,
refusal_reason); reports record only what a client sees. This joins the
two by request ID and gives median and p95 per stage, for answers and for
refusals apart, because a refusal at a gate never reaches generation.
Tokens are per request, summed over attempts: a rejected answer was billed
too. Runs without a matching trace are counted, never estimated. The log
itself stays local (evals/reports/ is ignored); the summary records its
SHA-256 next to the report it was joined with.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import report  # noqa: E402
from evals.checks import ANSWER, ERROR  # noqa: E402

STAGES = ("extract", "retrieve", "generate")


def read_traces(log: Path) -> dict[str, dict[str, Any]]:
    """Every /ask trace in a server log, by request ID; other lines are skipped."""
    traces = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event") == "ask" and "request_id" in record:
            traces[record["request_id"]] = record
    return traces


def percentile(values: list[float], p: float) -> float:
    """Nearest rank: the smallest value with at least p % of values at or below it."""
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)), 1) - 1]


def summarize(recorded: dict[str, Any], traces: dict[str, dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"answers": [], "refusals": []}
    reasons = Counter[str]()
    unmatched = errors = 0
    for case in recorded["cases"]:
        for run in case["runs"]:
            if run["outcome"] == ERROR:
                errors += 1
                continue
            rid = ((run.get("response") or {}).get("meta") or {}).get("request_id")
            if rid not in traces:
                unmatched += 1
                continue
            if run["outcome"] == ANSWER:
                groups["answers"].append(traces[rid])
            else:
                groups["refusals"].append(traces[rid])
                reasons[run["outcome"]] += 1
    result = {name: _group(found) for name, found in groups.items()}
    result["refusals"]["by_reason"] = dict(sorted(reasons.items()))
    result["unmatched_runs"] = unmatched
    result["technical_errors"] = errors
    return result


def _group(traces: list[dict[str, Any]]) -> dict[str, Any]:
    latency: dict[str, list[float]] = defaultdict(list)
    tokens: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for trace in traces:
        for stage in STAGES:
            if stage in trace.get("stages_ms", {}):
                latency[stage].append(trace["stages_ms"][stage])
        if "latency_ms" in trace:
            latency["total"].append(trace["latency_ms"])
        per_stage: dict[str, Counter[str]] = defaultdict(Counter)
        for usage in trace.get("usage", []):
            per_stage[usage["stage"]]["input"] += usage["input_tokens"]
            per_stage[usage["stage"]]["output"] += usage["output_tokens"]
        for stage, counts in per_stage.items():
            for kind in ("input", "output"):
                tokens[stage][kind].append(counts[kind])
    return {
        "requests": len(traces),
        "latency_ms": {
            stage: _stats(latency[stage]) for stage in (*STAGES, "total") if latency[stage]
        },
        "tokens": {
            stage: {kind: _stats(tokens[stage][kind]) for kind in ("input", "output")}
            for stage in STAGES
            if stage in tokens
        },
        "invalid_output": sum(len(t.get("invalid_output", [])) for t in traces),
    }


def _stats(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "p95": percentile(values, 95)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", required=True, type=Path, help="an end-to-end report")
    parser.add_argument("--log", required=True, type=Path, help="the server log of that run")
    parser.add_argument("--milestone", help="write evals/reports/milestones/<NAME>.json")
    args = parser.parse_args()

    recorded = json.loads(args.report.read_text(encoding="utf-8"))
    result = summarize(recorded, read_traces(args.log))
    result = {
        "provenance": {
            "report": {
                "path": str(args.report.resolve().relative_to(ROOT)),
                "sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(),
                "code": recorded["provenance"]["code"],
                "prompts": recorded["provenance"]["prompts"],
                "model": recorded["provenance"]["model"],
            },
            "log": {"sha256": hashlib.sha256(args.log.read_bytes()).hexdigest()},
        },
        **result,
    }
    for name in ("answers", "refusals"):
        group = result[name]
        print(f"{name}: {group['requests']} requests")
        for stage, stats in group["latency_ms"].items():
            print(f"  {stage:<9} {stats['median']:>8.1f} ms median  {stats['p95']:>8.1f} ms p95")
        for stage, kinds in group["tokens"].items():
            cells = ", ".join(f"{k} {v['median']:.0f}/{v['p95']:.0f}" for k, v in kinds.items())
            print(f"  {stage:<9} tokens median/p95: {cells}")
    unmatched, errors = result["unmatched_runs"], result["technical_errors"]
    print(f"unmatched runs {unmatched}, technical errors {errors}")
    path = report.write_report(result, name="stages", milestone=args.milestone)
    print(f"report: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
