"""How results are counted, printed and kept (SPEC §7.3).

A case runs several times. It passes only if every run passes and every
run has the same outcome; mixed results are "unstable", never "pass".
Content checks get the same per-case verdict, but decide the run by count:
in every repeat, at least 13 of 15 cases must pass them (SPEC §7.2).
Refusal errors are kept in a matrix and three separate counts. Exploratory
reports go to evals/reports/ (ignored by git); milestone reports go to
evals/reports/milestones/ and are committed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.checks import ANSWER, ERROR
from evals.sets import CaseSet, SetError

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "evals" / "reports"

PASS, FAIL, UNSTABLE = "pass", "fail", "unstable"
CONTENT_SHARE = (13, 15)
"""SPEC §7.2: 13 of 15 content checks, scaled up for a set of another size."""


def content_required(cases: int) -> int:
    passed, of = CONTENT_SHARE
    return -(-passed * cases // of)


def case_verdict(passed: list[bool], outcomes: list[str]) -> str:
    # A changing outcome is instability even when every run failed (SPEC §7.3).
    if len(set(outcomes)) > 1:
        return UNSTABLE
    if all(passed):
        return PASS
    if not any(passed):
        return FAIL
    return UNSTABLE


def summarize(cases: list[dict[str, Any]], endpoint_failures: list[str]) -> dict[str, Any]:
    verdicts = Counter(c["verdict"] for c in cases)
    passed = verdicts[PASS] == len(cases) and not endpoint_failures
    content = summarize_content(cases)
    if content is not None:
        passed = passed and all(n >= content["required"] for n in content["per_run"])
    return {
        "cases": len(cases),
        "pass": verdicts[PASS],
        "fail": verdicts[FAIL],
        "unstable": verdicts[UNSTABLE],
        "content": content,
        "verdict": PASS if passed else FAIL,
    }


def summarize_content(cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Content passes per repeat, and the cases that failed or wavered; None if unchecked."""
    if not cases or any(c.get("content") is None for c in cases):
        return None
    repeats = max(len(c["runs"]) for c in cases)
    per_run = [
        sum(bool(c["runs"][i]["content_passed"]) for c in cases if i < len(c["runs"]))
        for i in range(repeats)
    ]
    return {
        "per_run": per_run,
        "required": content_required(len(cases)),
        "fail": [c["id"] for c in cases if c["content"]["verdict"] == FAIL],
        "unstable": [c["id"] for c in cases if c["content"]["verdict"] == UNSTABLE],
    }


def refusal_matrix(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Expected outcome × actual outcome over every run of cases that pin one.

    False answers (refusal expected, answered), false refusals (answer
    expected, refused) and wrong refusal reasons are separate numbers and
    are never added up; technical errors are a fourth.
    """
    matrix: defaultdict[str, Counter[str]] = defaultdict(Counter)
    counts = Counter[str]()
    for case in cases:
        expected = case["expected_outcome"]
        if expected is None:
            continue
        for run in case["runs"]:
            actual = run["outcome"]
            matrix[expected][actual] += 1
            counts["runs"] += 1
            if actual == ERROR:
                counts["technical_errors"] += 1
            elif expected == ANSWER and actual != ANSWER:
                counts["false_refusals"] += 1
            elif expected != ANSWER and actual == ANSWER:
                counts["false_answers"] += 1
            elif expected not in (ANSWER, "refused", actual):
                counts["wrong_refusal_reason"] += 1
    return {
        "runs": counts["runs"],
        "false_answers": counts["false_answers"],
        "false_refusals": counts["false_refusals"],
        "wrong_refusal_reason": counts["wrong_refusal_reason"],
        "technical_errors": counts["technical_errors"],
        "matrix": {expected: dict(row) for expected, row in sorted(matrix.items())},
    }


def held_out_guard(case_set: CaseSet, milestone: str | None, reports_dir: Path) -> None:
    """A held-out set is evaluated once, and that run is a committed milestone."""
    if not case_set.held_out:
        return
    if milestone is None:
        raise SetError(
            f"{case_set.name!r} is held out: it runs only as a milestone (--milestone NAME), "
            "so its one result is committed"
        )
    for path in sorted((reports_dir / "milestones").glob("*.json")):
        recorded = json.loads(path.read_text(encoding="utf-8"))
        ran = recorded.get("provenance", {}).get("set", {})
        if ran.get("held_out") and ran.get("name") == case_set.name:
            raise SetError(
                f"held-out set {case_set.name!r} was already evaluated in {path}; "
                "it is evaluated once (SPEC §7.3)"
            )


def write_report(
    report: dict[str, Any],
    *,
    reports_dir: Path = REPORTS_DIR,
    name: str,
    milestone: str | None = None,
) -> Path:
    if milestone is not None:
        path = reports_dir / "milestones" / f"{milestone}.json"
        if path.exists():
            raise FileExistsError(f"{path} exists; a milestone report is never overwritten")
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = reports_dir / f"{stamp}-{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False, default=_json_default) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def print_cases(cases: list[dict[str, Any]]) -> None:
    width = max((len(c["id"]) for c in cases), default=0)
    for case in cases:
        latencies = [r["client_latency_ms"] for r in case["runs"]]
        timing = f"{max(latencies):>6} ms max" if latencies else ""
        content = case.get("content")
        mark = f"content {content['verdict']:<8} {content['passes']:>5}" if content else ""
        print(
            f"{case['verdict'].upper():<8}  {case['id']:<{width}}  {case['passes']:>5}  "
            f"{mark}  {timing}"
        )
        for failure in sorted({f for run in case["runs"] for f in run["failures"]}):
            print(f"          - {failure}")
        for failure in sorted({f for run in case["runs"] for f in run.get("content_failures", [])}):
            print(f"          - content: {failure}")


def print_summary(summary: dict[str, Any], matrix: dict[str, Any] | None) -> None:
    if matrix is not None:
        print(
            f"\nrefusals over {matrix['runs']} runs: "
            f"false answers {matrix['false_answers']}, "
            f"false refusals {matrix['false_refusals']}, "
            f"wrong reason {matrix['wrong_refusal_reason']}, "
            f"technical errors {matrix['technical_errors']}"
        )
        for expected, row in matrix["matrix"].items():
            cells = ", ".join(f"{actual} {n}" for actual, n in sorted(row.items()))
            print(f"  expected {expected:<14} got {cells}")
    content = summary.get("content")
    if content is not None:
        runs = ", ".join(f"{n}/{summary['cases']}" for n in content["per_run"])
        print(f"\ncontent checks per run: {runs} (need {content['required']} in every run)")
        if content["unstable"]:
            print(f"  unstable content: {', '.join(content['unstable'])}")
        if content["fail"]:
            print(f"  failing content: {', '.join(content['fail'])}")
    print(
        f"\n{summary['pass']}/{summary['cases']} cases pass; "
        f"{summary['fail']} fail, {summary['unstable']} unstable -> {summary['verdict'].upper()}"
    )


def _json_default(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
