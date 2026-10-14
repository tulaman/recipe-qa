"""Extraction-only eval: the first model call, on its own labelled set (SPEC §7.1).

    uv run python evals/extract.py --set smoke
    uv run python evals/extract.py --set golden --repeat 3 --milestone stage-5-extraction

Calls the configured provider's extract() in process (same settings as
the service: RECIPE_QA_PROVIDER, RECIPE_QA_MODEL, ANTHROPIC_API_KEY) for
every question in evals/cases/extraction/<set>.jsonl and compares the
result with the labelled Extraction field by field. Errors are counted
per field and per direction, because a missed exclusion and a spurious one
fail differently downstream (a false answer versus a false refusal).
Exits 1 unless every case matches its labels in every run.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import report  # noqa: E402
from evals.provenance import extraction_provenance  # noqa: E402
from evals.sets import CaseSet, SetError, load_extraction_set  # noqa: E402
from recipe_qa.config import Settings  # noqa: E402
from recipe_qa.providers import make_provider  # noqa: E402
from recipe_qa.providers.base import Provider, ProviderError  # noqa: E402
from recipe_qa.schemas import Extraction  # noqa: E402

DEFAULT_REPEAT = 3
FLAGS = ("in_domain", "safety_question")
SCALARS = ("max_total_minutes", "diet")
LISTS = ("must_include", "must_exclude")
FIELDS = (*FLAGS, *SCALARS, "time_limit_inclusive", *LISTS)


def compare(expected: Extraction, got: Extraction) -> list[dict[str, Any]]:
    """Every field where `got` differs from the labels, with the kind of error.

    Flags: false_positive / false_negative. Scalars: missed (label set, got
    null), spurious (label null, got a value), wrong (both set, differ).
    Lists: one missed or spurious entry per ingredient, ignoring order and
    case. time_limit_inclusive only counts when a time limit is labelled.
    """
    errors: list[dict[str, Any]] = []

    def error(field: str, kind: str, want: object, have: object) -> None:
        errors.append({"field": field, "kind": kind, "expected": want, "got": have})

    for field in FLAGS:
        want, have = getattr(expected, field), getattr(got, field)
        if want != have:
            error(field, "false_positive" if have else "false_negative", want, have)
    for field in SCALARS:
        want, have = getattr(expected, field), getattr(got, field)
        if want != have:
            kind = "missed" if have is None else "spurious" if want is None else "wrong"
            error(field, kind, want, have)
    if (
        expected.max_total_minutes is not None
        and expected.time_limit_inclusive != got.time_limit_inclusive
    ):
        error(
            "time_limit_inclusive", "wrong", expected.time_limit_inclusive, got.time_limit_inclusive
        )
    for field in LISTS:
        want_items = {i.strip().casefold() for i in getattr(expected, field)}
        have_items = {i.strip().casefold() for i in getattr(got, field)}
        for item in sorted(want_items - have_items):
            error(field, "missed", item, None)
        for item in sorted(have_items - want_items):
            error(field, "spurious", None, item)
    return errors


async def run_once(provider: Provider, case: dict[str, Any]) -> dict[str, Any]:
    try:
        completion = await provider.extract(case["question"])
    except ProviderError as exc:
        error = f"{type(exc).__name__}: {exc}"
        return {"passed": False, "error": error, "got": None, "errors": [], "signature": "error"}
    errors = compare(case["expect"], completion.value)
    return {
        "passed": not errors,
        "error": None,
        "got": completion.value.model_dump(),
        "errors": errors,
        "signature": completion.value.model_dump_json(),
    }


async def run_extraction(provider: Provider, case_set: CaseSet, repeat: int) -> dict[str, Any]:
    cases = []
    fields = {field: {"runs": 0, "errors": Counter[str]()} for field in FIELDS}
    for case in case_set.cases:
        runs = [await run_once(provider, case) for _ in range(repeat)]
        for run in runs:
            if run["error"] is not None:
                continue
            for field in FIELDS:
                fields[field]["runs"] += 1
            for error in run["errors"]:
                fields[error["field"]]["errors"][error["kind"]] += 1
        passed = [r["passed"] for r in runs]
        cases.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expect": case["expect"].model_dump(),
                "verdict": report.case_verdict(passed, [r.pop("signature") for r in runs]),
                "passes": f"{sum(passed)}/{len(runs)}",
                "runs": runs,
            }
        )
    return {
        "provenance": extraction_provenance(provider.model, case_set),
        "repeat": repeat,
        "summary": report.summarize(cases, []),
        "fields": {
            field: {"runs": stats["runs"], "errors": dict(stats["errors"])}
            for field, stats in fields.items()
        },
        "cases": cases,
    }


def print_result(result: dict[str, Any]) -> None:
    width = max((len(c["id"]) for c in result["cases"]), default=0)
    for case in result["cases"]:
        print(f"{case['verdict'].upper():<8}  {case['id']:<{width}}  {case['passes']:>5}")
        problems = {
            f"{e['field']} {e['kind']}: expected {e['expected']!r}, got {e['got']!r}"
            for run in case["runs"]
            for e in run["errors"]
        } | {run["error"] for run in case["runs"] if run["error"]}
        for problem in sorted(problems):
            print(f"          - {problem}")
    print("\nfield errors:")
    for field, stats in result["fields"].items():
        errors = ", ".join(f"{k} {n}" for k, n in sorted(stats["errors"].items())) or "none"
        print(f"  {field:<22} {errors}  (over {stats['runs']} runs)")
    report.print_summary(result["summary"], None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", default="smoke", help="evals/cases/extraction/<SET>.jsonl")
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument(
        "--milestone", help="write evals/reports/milestones/<NAME>.json, to be committed"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        case_set = load_extraction_set(args.set)
    except SetError as exc:
        parser.error(str(exc))

    settings = Settings.from_env()
    if settings.provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("set ANTHROPIC_API_KEY, or RECIPE_QA_PROVIDER=fake to check the runner")
    provider = make_provider(settings)
    result = asyncio.run(run_extraction(provider, case_set, args.repeat))
    print_result(result)
    path = report.write_report(result, name=f"extraction-{case_set.name}", milestone=args.milestone)
    print(f"report: {path.relative_to(ROOT)}")
    sys.exit(0 if result["summary"]["verdict"] == report.PASS else 1)


if __name__ == "__main__":
    main()
