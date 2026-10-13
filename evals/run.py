"""Eval harness: send cases to a running instance over HTTP (SPEC §7).

    uv run python evals/run.py --base-url http://localhost:8000
    uv run python evals/run.py --base-url https://... --cases evals/smoke.json
    uv run python evals/run.py --base-url http://localhost:8000 --contract-only

Every response is checked against the contract (schema, invariants I1-I5,
citations resolve to manifest URLs) and, unless --contract-only, against
the case's expectations. --contract-only is for an instance running the
fake provider, whose answers are meaningless but whose shape is not.

Writes evals/reports/<UTC timestamp>.json; exits 1 if any check fails.
Stage 3 skeleton: contract + expected refusal + expected sources. Facts,
forbidden claims, constraint predicates and recall@5 arrive in stage 5.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recipe_qa.corpus import load_manifest  # noqa: E402
from recipe_qa.schemas import AskResponse  # noqa: E402

MANIFEST = load_manifest(ROOT / "data" / "manifest.json")
ID_BY_URL = {r.url: r.id for r in MANIFEST.recipes}
TITLE_BY_URL = {r.url: r.title for r in MANIFEST.recipes}


def recorded_body(response: httpx.Response | None) -> object:
    """The response as JSON, or its raw text when a proxy answered with HTML."""
    if response is None or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def check_endpoints(client: httpx.Client) -> list[str]:
    try:
        return _check_endpoints(client)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        # Non-JSON bodies (ValueError) and JSON of the wrong shape, e.g. a
        # proxy answering [] (AttributeError/TypeError), are failures too.
        return [f"endpoints: {type(exc).__name__}: {exc}"]


def _check_endpoints(client: httpx.Client) -> list[str]:
    failures = []
    health = client.get("/healthz")
    if health.status_code != 200 or health.json().get("status") != "ok":
        failures.append(f"/healthz: HTTP {health.status_code}")
    elif health.json().get("corpus_version") != MANIFEST.corpus_version:
        failures.append("/healthz: corpus_version differs from the local manifest")
    schema = client.get("/schema")
    if schema.status_code != 200 or schema.json() != AskResponse.model_json_schema():
        failures.append("/schema: differs from AskResponse")
    ui = client.get("/")
    if ui.status_code != 200 or "<html" not in ui.text.lower():
        failures.append(f"/: UI not served (HTTP {ui.status_code})")
    return failures


def check_contract(response: httpx.Response) -> tuple[AskResponse | None, list[str]]:
    failures = []
    if response.status_code != 200:
        return None, [f"HTTP {response.status_code}: {response.text[:200]}"]
    try:
        body = AskResponse.model_validate_json(response.content)  # I1, I2, I4, I5
    except ValidationError as exc:
        return None, [f"schema/invariants: {exc.errors()[0]['msg']}"]
    if response.headers.get("X-Request-ID") != body.meta.request_id:
        failures.append("X-Request-ID header differs from meta.request_id")
    for citation in body.citations:  # I3
        if citation.url not in ID_BY_URL:
            failures.append(f"citation outside the corpus: {citation.url}")
        elif citation.title != TITLE_BY_URL[citation.url]:
            failures.append(f"citation title {citation.title!r} is not the manifest's for that URL")
    if body.meta.corpus_version != MANIFEST.corpus_version:
        failures.append("meta.corpus_version differs from the local manifest")
    return body, failures


def check_expectations(body: AskResponse, expect: dict[str, Any]) -> list[str]:
    failures = []
    if "refused" in expect and body.refused != expect["refused"]:
        failures.append(f"refused={body.refused}, expected {expect['refused']}")
    if "refusal_reason" in expect and body.refusal_reason != expect["refusal_reason"]:
        expected = expect["refusal_reason"]
        failures.append(f"refusal_reason={body.refusal_reason}, expected {expected}")
    cited = {ID_BY_URL.get(c.url) for c in body.citations}
    if "sources_any" in expect and not cited & set(expect["sources_any"]):
        got = sorted(filter(None, cited))
        failures.append(f"cited {got}, expected one of {expect['sources_any']}")
    return failures


def run(base_url: str, cases_path: Path, contract_only: bool) -> int:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = []
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        endpoint_failures = check_endpoints(client)
        for case in cases:
            started = time.perf_counter()
            try:
                response = client.post("/ask", json={"question": case["question"]})
            except httpx.HTTPError as exc:
                # One unreachable case must not cost the report for the others.
                response, failures = None, [f"request failed: {type(exc).__name__}: {exc}"]
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            if response is not None:
                body, failures = check_contract(response)
                if body is not None and not contract_only:
                    failures += check_expectations(body, case.get("expect", {}))
            results.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "status": response.status_code if response is not None else None,
                    "client_latency_ms": elapsed_ms,
                    "passed": not failures,
                    "failures": failures,
                    "response": recorded_body(response),
                }
            )

    width = max(len(r["id"]) for r in results)
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        status = r["status"] or "---"
        print(f"{mark}  {r['id']:<{width}}  {status}  {r['client_latency_ms']:>6} ms")
        for failure in r["failures"]:
            print(f"      - {failure}")
    for failure in endpoint_failures:
        print(f"FAIL  endpoint: {failure}")
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} cases passed; {len(endpoint_failures)} endpoint failures")

    reports = ROOT / "evals" / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    report = {
        "base_url": base_url,
        "cases_file": str(cases_path.relative_to(ROOT)),
        "contract_only": contract_only,
        "endpoint_failures": endpoint_failures,
        "results": results,
    }
    (reports / f"{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if passed == len(results) and not endpoint_failures else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "smoke.json")
    parser.add_argument("--contract-only", action="store_true")
    args = parser.parse_args()
    sys.exit(run(args.base_url.rstrip("/"), args.cases.resolve(), args.contract_only))


if __name__ == "__main__":
    main()
