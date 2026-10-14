"""Recount a recorded end-to-end report by check category, offline (SPEC §7.2).

    uv run python evals/rescore.py evals/reports/milestones/stage-4b-golden-baseline.json
    uv run python evals/rescore.py evals/reports/milestones/*-golden*.json

Reads the response bodies a report recorded and checks them again against
the *current* case set of the same name: refusal, sources, constraints and
content, each counted on its own. Contract is taken from the recorded run,
because the headers it was checked against are not recorded. No network,
no provider: this is how reports written before the harness scored content
are compared with later ones. It prints; it never writes a report, and a
milestone file is never changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.checks import (  # noqa: E402
    ERROR,
    check_constraints,
    check_content,
    check_refusal,
    check_sources,
)
from evals.report import content_required  # noqa: E402
from evals.sets import CaseSet, load_e2e_set  # noqa: E402
from recipe_qa.schemas import AskResponse  # noqa: E402

CATEGORIES = ("contract", "refusal", "sources", "constraints", "content")
CHECKS = {
    "refusal": check_refusal,
    "sources": check_sources,
    "constraints": check_constraints,
    "content": check_content,
}


def rescore(recorded: dict[str, Any], case_set: CaseSet) -> dict[str, Any]:
    expects = {c["id"]: c.get("expect", {}) for c in case_set.cases}
    counts = {name: Counter[str]() for name in CATEGORIES}
    per_run: Counter[int] = Counter()
    missing = []
    for case in recorded["cases"]:
        if case["id"] not in expects:
            missing.append(case["id"])
            continue
        expect = expects[case["id"]]
        for i, run in enumerate(case["runs"]):
            body = _parsed(run)
            results = {"contract": body is not None and run["outcome"] != ERROR}
            for name, check in CHECKS.items():
                results[name] = results["contract"] and not check(body, expect)  # type: ignore[arg-type]
            for name, ok in results.items():
                counts[name]["runs"] += 1
                counts[name]["passed"] += ok
            per_run[i] += results["content"]
    repeats = max((len(c["runs"]) for c in recorded["cases"]), default=0)
    return {
        "categories": {
            name: {"passed": counts[name]["passed"], "runs": counts[name]["runs"]}
            for name in CATEGORIES
        },
        "content": {
            "per_run": [per_run[i] for i in range(repeats)],
            "required": content_required(len(case_set.cases)),
        },
        "set_changed": recorded["provenance"]["set"].get("sha256") != case_set.sha256,
        "missing_cases": missing,
    }


def _parsed(run: dict[str, Any]) -> AskResponse | None:
    body = run.get("response")
    if not isinstance(body, dict) or "answer" not in body:
        return None
    try:
        return AskResponse.model_validate(body)
    except ValidationError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    header = "".join(f"{name:>13}" for name in CATEGORIES)
    print(f"{'report':<40}{header}  content per run")
    for path in args.reports:
        recorded = json.loads(path.read_text(encoding="utf-8"))
        table = rescore(recorded, load_e2e_set(recorded["provenance"]["set"]["name"]))
        cells = "".join(
            f"{table['categories'][n]['passed']:>8}/{table['categories'][n]['runs']:<4}"
            for n in CATEGORIES
        )
        runs = ", ".join(str(n) for n in table["content"]["per_run"])
        print(f"{path.stem:<40}{cells}  {runs} (need {table['content']['required']})")
        if table["set_changed"]:
            print(f"{'':<40}case set changed since the run; rescored against the current one")
        if table["missing_cases"]:
            print(f"{'':<40}not in the current set: {', '.join(table['missing_cases'])}")


if __name__ == "__main__":
    main()
