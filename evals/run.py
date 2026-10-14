"""End-to-end eval: send a case set to a running instance over HTTP (SPEC §7).

    uv run python evals/run.py --base-url http://localhost:8000
    uv run python evals/run.py --base-url https://... --set smoke --repeat 3
    uv run python evals/run.py --base-url http://localhost:8000 --contract-only
    uv run python evals/run.py --base-url https://... --set golden --milestone stage-5-golden

Every response is checked against the contract (schema, invariants I1-I5,
citations resolve to manifest URLs) and, unless --contract-only, against
the case's expectations, including constraint predicates over corpus
metadata. --contract-only is for an instance running the fake provider,
whose answers are meaningless but whose shape is not.

Each case runs --repeat times; a case passes only if every run passes
with the same outcome. Writes a report with provenance, per-case
stability and the refusal matrix (evals/README.md); exits 1 unless every
case passes and every endpoint check holds.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import report  # noqa: E402
from evals.checks import (  # noqa: E402
    ERROR,
    check_contract,
    check_endpoints,
    check_expectations,
    expected_outcome,
    outcome,
    recorded_body,
)
from evals.provenance import e2e_provenance  # noqa: E402
from evals.sets import CaseSet, SetError, load_e2e_set  # noqa: E402

DEFAULT_REPEAT = 3
RATE_LIMIT_WAIT_S = 61.0
"""The service allows 30 requests per minute per client (SPEC §8); a 429
means wait out the window, not a failed case."""
RATE_LIMIT_RETRIES = 2


def ask(
    client: httpx.Client, question: str, sleep: Callable[[float], None]
) -> tuple[httpx.Response, int]:
    """POST /ask, waiting out rate limits; returns the response and the waits."""
    waits = 0
    while True:
        response = client.post("/ask", json={"question": question})
        if response.status_code != 429 or waits == RATE_LIMIT_RETRIES:
            return response, waits
        waits += 1
        sleep(RATE_LIMIT_WAIT_S)


def run_once(
    client: httpx.Client,
    case: dict[str, Any],
    contract_only: bool,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    started = time.perf_counter()
    response: httpx.Response | None = None
    waits = 0
    try:
        response, waits = ask(client, case["question"], sleep)
    except httpx.HTTPError as exc:
        # One unreachable case must not cost the report for the others.
        failures = [f"request failed: {type(exc).__name__}: {exc}"]
        body, broken = None, True
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if response is not None:
        body, failures = check_contract(response)
        # A response that breaks the contract is a technical error, even when
        # it parses: it must not count as an answer or a refusal.
        broken = bool(failures)
        if body is not None and not contract_only:
            failures += check_expectations(body, case.get("expect", {}))
    return {
        "status": response.status_code if response is not None else None,
        "client_latency_ms": elapsed_ms,
        "rate_limit_waits": waits,
        "outcome": ERROR if broken else outcome(body),
        "model": body.meta.model if body is not None else None,
        "passed": not failures,
        "failures": failures,
        "response": recorded_body(response),
    }


def run_set(
    client: httpx.Client,
    case_set: CaseSet,
    *,
    repeat: int,
    contract_only: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    endpoint_failures, health = check_endpoints(client)
    cases = []
    for case in case_set.cases:
        runs = [run_once(client, case, contract_only, sleep) for _ in range(repeat)]
        passed = [r["passed"] for r in runs]
        cases.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expected_outcome": None
                if contract_only
                else expected_outcome(case.get("expect", {})),
                "verdict": report.case_verdict(passed, [r["outcome"] for r in runs]),
                "passes": f"{sum(passed)}/{len(runs)}",
                "runs": runs,
            }
        )
    models = [r["model"] for c in cases for r in c["runs"] if r["model"]]
    return {
        "provenance": e2e_provenance(health, models, case_set),
        "base_url": str(client.base_url),
        "mode": "contract-only" if contract_only else "full",
        "repeat": repeat,
        "summary": report.summarize(cases, endpoint_failures),
        "endpoint_failures": endpoint_failures,
        "refusal_matrix": None if contract_only else report.refusal_matrix(cases),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--set", default="smoke", help="case set name in evals/cases/")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument(
        "--milestone", help="write evals/reports/milestones/<NAME>.json, to be committed"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        case_set = load_e2e_set(args.set)
        report.held_out_guard(case_set, args.milestone, report.REPORTS_DIR)
    except SetError as exc:
        parser.error(str(exc))

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
        result = run_set(client, case_set, repeat=args.repeat, contract_only=args.contract_only)

    report.print_cases(result["cases"])
    for failure in result["endpoint_failures"]:
        print(f"FAIL      endpoint: {failure}")
    report.print_summary(result["summary"], result["refusal_matrix"])
    path = report.write_report(result, name=f"e2e-{case_set.name}", milestone=args.milestone)
    print(f"report: {path.relative_to(ROOT)}")
    sys.exit(0 if result["summary"]["verdict"] == report.PASS else 1)


if __name__ == "__main__":
    main()
