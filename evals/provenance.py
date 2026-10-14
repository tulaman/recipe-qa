"""The versions a report was produced against (SPEC §7.3).

Each entry has a value and says whether the harness could confirm it.
Over HTTP, the build and corpus come from /healthz and the model from
meta.model. The service does not publish its prompt version, but /healthz
names the commit it was built from: when that commit is in this
repository and recipe_qa/prompts/ here is identical to it, the locally
computed PROMPT_VERSION is the server's. Otherwise it is marked
unconfirmed rather than assumed.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.checks import MANIFEST
from evals.sets import CaseSet
from recipe_qa.generation import PROMPT_VERSION

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = "recipe_qa/prompts"
SERVED_PATHS = ("recipe_qa", "data")
"""What decides an answer: a dirty tree here means the local code is not a commit."""


def git(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return 127, ""
    return result.returncode, result.stdout.strip()


def harness_state() -> dict[str, Any]:
    code, sha = git("rev-parse", "HEAD")
    if code != 0:
        return {"git_sha": None, "dirty": None}
    code, status = git("status", "--porcelain", "--", *SERVED_PATHS)
    return {"git_sha": sha, "dirty": bool(status) if code == 0 else None}


def e2e_provenance(
    health: dict[str, Any] | None, models: list[str], case_set: CaseSet
) -> dict[str, Any]:
    health = health or {}
    build = health.get("version")
    corpus = health.get("corpus_version")
    return {
        "created_at": _now(),
        "code": {"value": build, "source": "GET /healthz version"},
        "prompts": _prompts_of_build(build),
        "model": {"value": sorted(set(models)), "source": "meta.model of every response"},
        "corpus": {
            "value": corpus,
            "confirmed": corpus == MANIFEST.corpus_version,
            "note": "matches data/manifest.json in this checkout"
            if corpus == MANIFEST.corpus_version
            else "differs from data/manifest.json in this checkout",
        },
        "set": case_set.describe(),
        "harness": harness_state(),
    }


def extraction_provenance(model: str, case_set: CaseSet) -> dict[str, Any]:
    harness = harness_state()
    return {
        "created_at": _now(),
        "code": {"value": harness["git_sha"], "dirty": harness["dirty"], "source": "this checkout"},
        "prompts": {
            "value": PROMPT_VERSION,
            "confirmed": True,
            "note": "computed from the prompts this process sent",
        },
        "model": {"value": model, "source": "provider.model"},
        "corpus": {
            "value": MANIFEST.corpus_version,
            "confirmed": True,
            "note": "data/manifest.json in this checkout",
        },
        "set": case_set.describe(),
        "harness": harness,
    }


def _prompts_of_build(build: str | None) -> dict[str, Any]:
    def unconfirmed(note: str) -> dict[str, Any]:
        return {"value": PROMPT_VERSION, "confirmed": False, "note": note}

    if not build or build == "dev":
        return unconfirmed(f"server reports build {build!r}; prompt version is this checkout's")
    code, _ = git("diff", "--quiet", build, "--", PROMPTS)
    if code == 0:
        return {
            "value": PROMPT_VERSION,
            "confirmed": True,
            "note": f"{PROMPTS}/ in this checkout is identical to build {build}",
        }
    if code == 1:
        return unconfirmed(f"{PROMPTS}/ in this checkout differs from build {build}")
    return unconfirmed(f"build {build} is not in this repository; fetch it to confirm")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
