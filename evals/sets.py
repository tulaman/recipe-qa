"""Named case sets, and the rule that keeps a held-out set apart (SPEC §7.3).

An end-to-end set is `evals/cases/<name>.json`:

    {"description": "...", "held_out": false, "cases": [{"id", "question", "expect"}]}

An extraction set is `evals/cases/extraction/<name>.jsonl`, one labelled
question per line: {"id", "question", "expect": <Extraction fields>}.

Every load checks the whole directory: a question that appears in a
held-out set and in any other set is an error, so tuning on one set can
never be tuning on the held-out one.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from evals.checks import validate_constraints
from recipe_qa.schemas import Extraction

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "evals" / "cases"

Kind = Literal["e2e", "extraction"]


class SetError(Exception):
    """A set that cannot be used as asked: malformed, overlapping or guarded."""


@dataclass(frozen=True)
class CaseSet:
    name: str
    kind: Kind
    path: Path
    held_out: bool
    description: str
    cases: list[dict[str, Any]]
    sha256: str

    def describe(self) -> dict[str, Any]:
        """What a report records about the set it ran."""
        try:
            path = str(self.path.relative_to(ROOT))
        except ValueError:
            path = str(self.path)
        return {
            "name": self.name,
            "kind": self.kind,
            "path": path,
            "sha256": self.sha256,
            "held_out": self.held_out,
            "cases": len(self.cases),
        }


def load_e2e_set(name: str, cases_dir: Path = CASES_DIR) -> CaseSet:
    path = cases_dir / f"{name}.json"
    case_set = _read_e2e(path)
    for case in case_set.cases:
        try:
            validate_constraints(case.get("expect", {}).get("constraints", {}))
        except ValueError as exc:
            raise SetError(f"{path.name}: case {case['id']}: {exc}") from exc
    check_disjoint(cases_dir)
    return case_set


def load_extraction_set(name: str, cases_dir: Path = CASES_DIR) -> CaseSet:
    path = cases_dir / "extraction" / f"{name}.jsonl"
    case_set = _read_extraction(path)
    check_disjoint(cases_dir)
    return case_set


def check_disjoint(cases_dir: Path = CASES_DIR) -> None:
    """Raise if any held-out question also appears in another set."""
    sets = [_read_e2e(p) for p in sorted(cases_dir.glob("*.json"))]
    sets += [_read_extraction(p) for p in sorted(cases_dir.glob("extraction/*.jsonl"))]
    held_out = {_normalized(c["question"]): s.name for s in sets if s.held_out for c in s.cases}
    for case_set in sets:
        if case_set.held_out:
            continue
        for case in case_set.cases:
            owner = held_out.get(_normalized(case["question"]))
            if owner:
                raise SetError(
                    f"{case_set.kind} set {case_set.name!r}, case {case['id']!r}: the question "
                    f"belongs to held-out set {owner!r} and must not be used for tuning"
                )


def _read_e2e(path: Path) -> CaseSet:
    raw = _read(path)
    try:
        data = json.loads(raw)
        cases = data["cases"]
        held_out = bool(data.get("held_out", False))
        description = str(data.get("description", ""))
    except (ValueError, KeyError, TypeError) as exc:
        raise SetError(f"{path.name}: not a case set object: {exc}") from exc
    return _case_set(path, path.stem, "e2e", held_out, description, cases, raw)


def _read_extraction(path: Path) -> CaseSet:
    raw = _read(path)
    cases = []
    for number, line in enumerate(raw.decode().splitlines(), start=1):
        if not line.strip():
            continue
        row: Any = None
        try:
            row = json.loads(line)
            row["expect"] = Extraction.model_validate(row["expect"])
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            where = row.get("id", f"line {number}") if isinstance(row, dict) else f"line {number}"
            raise SetError(f"{path.name}: {where}: {exc}") from exc
        cases.append(row)
    # Extraction labels are tuning data by nature; held-out sets are end-to-end.
    return _case_set(path, path.stem, "extraction", False, "", cases, raw)


def _case_set(
    path: Path,
    name: str,
    kind: Kind,
    held_out: bool,
    description: str,
    cases: list[dict[str, Any]],
    raw: bytes,
) -> CaseSet:
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case.get("id"), str) or not isinstance(case.get("question"), str):
            raise SetError(f"{path.name}: every case needs a string id and question: {case}")
        if case["id"] in seen:
            raise SetError(f"{path.name}: duplicate case id {case['id']!r}")
        seen.add(case["id"])
    return CaseSet(
        name=name,
        kind=kind,
        path=path,
        held_out=held_out,
        description=description,
        cases=cases,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise SetError(f"no case set at {path}") from exc


def _normalized(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().casefold()
