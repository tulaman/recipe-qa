"""Recipe-summary `time` → total minutes (SPEC §4, ADR 004).

Every input below is a real value seen on Wikibooks Cookbook pages.
Rule of thumb under test: when in doubt, return unknown — an unknown time
is never eligible for a time constraint, a wrong one silently is.
"""

import pytest

from recipe_qa.ingestion.timeparse import parse_total_minutes


@pytest.mark.parametrize(
    ("raw", "minutes"),
    [
        ("1 hour", 60),
        ("15 minutes", 15),
        ("30&nbsp;minutes", 30),
        ("½ hour", 30),
        ("1½ hours", 90),
        ("1¼ hours", 75),
        ("3 &frac12; hours", 210),
        ("1 hour 30 minutes", 90),
        ("90 minutes", 90),
        ("2 min", 2),
        ("1 hr", 60),
        # Ranges use the upper bound.
        ("2–3 hours", 180),
        ("45–60 minutes", 60),
        ("15 - 20 minutes", 20),
        ("20-25 minutes", 25),
        ("1.5–2 hours", 120),
        ("10 to 15 minutes", 15),
        # Approximations keep the stated value.
        ("About 1 hour", 60),
        ("~10 minutes", 10),
        ("< 30 minutes", 30),
        # A parenthetical breakdown or "including" note does not change the total.
        ("90 minutes (40 minutes active kitchen time, 50 minutes cooking time)", 90),
        ("2 hours (including resting and baking)", 120),
        ("30 minutes, including prep time", 30),
    ],
)
def test_single_stated_time_is_explicit(raw: str, minutes: int) -> None:
    fact = parse_total_minutes(raw)
    assert fact.value == minutes
    assert fact.status == "explicit"
    assert fact.evidence == raw


@pytest.mark.parametrize(
    ("raw", "minutes"),
    [
        ("Prep: 20 minutes<br/>Baking: 60 minutes", 80),
        ("Prep: 30 minutes<br>Cooking: ~30 minutes", 60),
        ("prep: 20 minutes<br/>baking: 35 minutes", 55),
        ("Prep: ~15 minutes<br/>Baking: 20-25 minutes", 40),
        ("30 minutes + 24 hours", 1470),
        ("15 minutes + 1 hour standing", 75),
        ("15 minutes + 1 hour refrigeration", 75),
        ("Prep: 1 hour<br>Chilling: 4 hours", 300),
        # Malformed line break seen on real pages.
        ("Prep: 15 minutes</br>Cooking: 15 minutes", 30),
    ],
)
def test_stages_are_summed_and_marked_derived(raw: str, minutes: int) -> None:
    fact = parse_total_minutes(raw)
    assert fact.value == minutes
    assert fact.status == "derived"
    assert fact.evidence == raw


def test_explicit_total_line_wins_over_stages() -> None:
    fact = parse_total_minutes("Prep: ½ hour<br>Total: 1 hour")
    assert (fact.value, fact.status) == (60, "explicit")


@pytest.mark.parametrize(
    "raw",
    [
        # Quantity per batch, not a total.
        "Prep: 10 minutes<br/>Cooking: 5 minutes per skillet",
        "Prep: overnight<br/>Cooking: 2–3 minutes each",
        # Unquantified stage.
        "30 minutes + cooling",
        "10–12 minutes (plus refrigeration time)",
        "½ hour, plus a few minutes prep",
        "Cooking: 1 hour 15 minutes<br>Resting: Overnight to 3 days",
        "2 hours to overnight",
        # Multi-day units are not converted.
        "3–6 days",
        "1 month",
        "Prep: 15 minutes<br/>Fermentation: 2–3 days",
        # One stage only; the others are not stated.
        "Prep: 10 minutes",
        # No unit.
        "30",
        "1 hour 30",
        "quick",
    ],
)
def test_ambiguous_time_is_unknown_but_keeps_evidence(raw: str) -> None:
    fact = parse_total_minutes(raw)
    assert fact.value is None
    assert fact.status == "unknown"
    assert fact.evidence == raw


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_time_is_unknown_without_evidence(raw: str | None) -> None:
    fact = parse_total_minutes(raw)
    assert (fact.value, fact.status, fact.evidence) == (None, "unknown", None)
