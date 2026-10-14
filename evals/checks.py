"""What one end-to-end response is checked against (SPEC §7.2).

Contract checks hold for every response. Expectation checks compare a
response with its case: refusal, sources, and constraint predicates. The
predicates read corpus metadata (SPEC §4), never the answer text, and are
written here independently of the service so they can catch its mistakes.
Content checks read the answer text and the citations; they are kept
apart from the others because a run passes on 13 of 15 of them, not on
all (SPEC §7.2).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from recipe_qa.corpus import Recipe, load_manifest, load_recipes
from recipe_qa.schemas import AskResponse

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = load_manifest(ROOT / "data" / "manifest.json")
RECIPES = {r.id: r for r in load_recipes(ROOT / "data" / "recipes.jsonl")}
ID_BY_URL = {r.url: r.id for r in MANIFEST.recipes}
TITLE_BY_URL = {r.url: r.title for r in MANIFEST.recipes}

ANSWER = "answer"
ERROR = "error"
"""Outcome labels besides the refusal reasons themselves."""

CONSTRAINT_KEYS = frozenset(
    {"max_total_minutes", "time_limit_inclusive", "diet", "must_include", "must_exclude"}
)
DIETS = frozenset({"vegetarian", "vegan", "gluten_free"})


def recorded_body(response: httpx.Response | None) -> object:
    """The response as JSON, or its raw text when a proxy answered with HTML."""
    if response is None or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def check_endpoints(client: httpx.Client) -> tuple[list[str], dict[str, Any] | None]:
    """Failures of /healthz, /schema and /, and the /healthz body if it parsed."""
    health: dict[str, Any] | None = None
    try:
        response = client.get("/healthz")
        body = response.json()
        health = body if isinstance(body, dict) else None
        return _check_endpoints(client, response, health), health
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        # Non-JSON bodies (ValueError) and JSON of the wrong shape, e.g. a
        # proxy answering [] (AttributeError/TypeError), are failures too.
        return [f"endpoints: {type(exc).__name__}: {exc}"], health


def _check_endpoints(
    client: httpx.Client, health: httpx.Response, body: dict[str, Any] | None
) -> list[str]:
    failures = []
    if health.status_code != 200 or body is None or body.get("status") != "ok":
        failures.append(f"/healthz: HTTP {health.status_code}")
    elif body.get("corpus_version") != MANIFEST.corpus_version:
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


def outcome(body: AskResponse | None) -> str:
    """ "answer", a refusal reason, or "error" for anything that broke the contract."""
    if body is None:
        return ERROR
    return body.refusal_reason or ANSWER


def expected_outcome(expect: dict[str, Any]) -> str | None:
    """The outcome a case pins, or None when it does not say."""
    if "refused" not in expect:
        return None
    if not expect["refused"]:
        return ANSWER
    return expect.get("refusal_reason", "refused")


def check_expectations(body: AskResponse, expect: dict[str, Any]) -> list[str]:
    return [
        *check_refusal(body, expect),
        *check_sources(body, expect),
        *check_constraints(body, expect),
    ]


def check_refusal(body: AskResponse, expect: dict[str, Any]) -> list[str]:
    failures = []
    if "refused" in expect and body.refused != expect["refused"]:
        failures.append(f"refused={body.refused}, expected {expect['refused']}")
    if "refusal_reason" in expect and body.refusal_reason != expect["refusal_reason"]:
        expected = expect["refusal_reason"]
        failures.append(f"refusal_reason={body.refusal_reason}, expected {expected}")
    return failures


def check_sources(body: AskResponse, expect: dict[str, Any]) -> list[str]:
    cited = cited_ids(body)
    if "sources_any" in expect and not set(cited) & set(expect["sources_any"]):
        return [f"cited {sorted(cited)}, expected one of {expect['sources_any']}"]
    return []


def check_constraints(body: AskResponse, expect: dict[str, Any]) -> list[str]:
    constraints = expect.get("constraints")
    if not constraints:
        return []
    return [
        f"cited {recipe_id}: {failure}"
        for recipe_id in cited_ids(body)
        for failure in constraint_failures(RECIPES[recipe_id], constraints)
    ]


def check_content(body: AskResponse, expect: dict[str, Any]) -> list[str]:
    """expected_facts, forbidden_claims and sources_all (SPEC §7.2).

    A case without them passes on the right outcome; a wrong outcome fails
    content too, since an unexpected refusal says nothing that was asked.
    """
    failures = []
    expected, actual = expected_outcome(expect), outcome(body)
    if expected is not None and actual != expected and not (expected == "refused" and body.refused):
        failures.append(f"outcome {actual}, expected {expected}")
    answer = body.answer or ""
    for fact in expect.get("expected_facts", []):
        if not re.search(fact, answer, re.IGNORECASE):
            failures.append(f"expected fact {fact!r} not in answer")
    for claim in expect.get("forbidden_claims", []):
        if re.search(claim, answer, re.IGNORECASE):
            failures.append(f"forbidden claim {claim!r} in answer")
    cited = cited_ids(body)
    failures += [f"{rid} not cited" for rid in expect.get("sources_all", []) if rid not in cited]
    return failures


def cited_ids(body: AskResponse) -> list[str]:
    return [ID_BY_URL[c.url] for c in body.citations if c.url in ID_BY_URL]


# --- constraint predicates (SPEC §4) -------------------------------------------


def validate_constraints(constraints: dict[str, Any]) -> None:
    unknown = set(constraints) - CONSTRAINT_KEYS
    if unknown:
        raise ValueError(f"unknown constraint keys {sorted(unknown)}")
    diet = constraints.get("diet")
    if diet is not None and diet not in DIETS:
        raise ValueError(f"unknown diet {diet!r}")


def constraint_failures(recipe: Recipe, constraints: dict[str, Any]) -> list[str]:
    """Why `recipe` does not satisfy `constraints`; empty when it does.

    Keys are the Extraction fields. `must_include` / `must_exclude` items
    are an ingredient or a list of alternatives ("walnut", ["nut", "walnut",
    "almond"]); each is matched as a whole word, singular or plural, in the
    ingredient line and its normalized name. The case lists its aliases
    itself, so the check does not share the service's alias table.
    """
    validate_constraints(constraints)
    failures = []
    meta = recipe.metadata

    limit = constraints.get("max_total_minutes")
    if limit is not None:
        inclusive = constraints.get("time_limit_inclusive", False)
        minutes = meta.total_minutes.value
        within = minutes is not None and (minutes <= limit if inclusive else minutes < limit)
        if not within:
            bound = f"{'≤' if inclusive else '<'} {limit}"
            failures.append(f"total time {minutes} min ({meta.total_minutes.status}), need {bound}")

    diet = constraints.get("diet")
    if diet is not None:
        fact = getattr(meta.diet, diet)
        # Gluten-free has no derivation: only an explicit category vouches (SPEC §4).
        if fact.value is not True or (diet == "gluten_free" and fact.status != "explicit"):
            failures.append(f"not {diet} ({fact.value}, {fact.status})")

    for item in constraints.get("must_include", []):
        if not _matching_lines(recipe, item):
            failures.append(f"no ingredient matches {item!r}")

    for item in constraints.get("must_exclude", []):
        # Optional ingredients count as present (SPEC §4).
        for line in _matching_lines(recipe, item):
            failures.append(f"contains {line!r}, excluded by {item!r}")

    return failures


def _matching_lines(recipe: Recipe, item: str | list[str]) -> list[str]:
    alternatives = [item] if isinstance(item, str) else item
    patterns = [
        re.compile(rf"\b{re.escape(word.lower())}(?:s|es)?\b", re.IGNORECASE)
        for word in alternatives
    ]
    return [
        ingredient.raw
        for ingredient in recipe.ingredients
        if any(p.search(ingredient.raw) or p.search(ingredient.normalized) for p in patterns)
    ]
