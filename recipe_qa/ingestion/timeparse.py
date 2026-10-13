"""Parse the `time` field of {{Recipe summary}} into total minutes.

Rules (SPEC §4, ADR 004), in order:

1. Missing or blank → unknown, no evidence.
2. A parenthetical that adds unstated time ("plus refrigeration time",
   "excluding ...") → unknown. Any other parenthetical is a breakdown or a
   note and is ignored.
3. The value is split into stages on line breaks, "+", "plus", commas and
   semicolons; an "including ..." note is dropped. A stage that is not a
   total ("overnight", "days", "per skillet") or has no stated duration
   ("+ cooling", "a few minutes prep") makes the whole value unknown. So
   does a stage with a bare number and no unit.
4. Inside a stage, "1 hour 30 minutes" adds up and a range ("15–20") uses
   its upper bound. "About", "~" and "<" keep the stated value.
5. A stage labelled "Total:" is the answer (explicit). A single unlabelled
   stage is explicit. Several stages are summed and marked derived. A single
   stage labelled anything else ("Prep: 10 minutes") is unknown, because the
   other stages are not stated.

The original string is always kept as evidence so a reviewer can audit it.
"""

import html
import re

from recipe_qa.corpus import Fact

_FRACTIONS = {"½": 1 / 2, "¼": 1 / 4, "¾": 3 / 4, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 1 / 8}
_FRACTION_CHARS = "".join(_FRACTIONS)
_NUM = rf"(?:\d+(?:\.\d+)?\s*[{_FRACTION_CHARS}]|[{_FRACTION_CHARS}]|\d+(?:\.\d+)?)"
_UNIT_MINUTES = {"hour": 60, "hr": 60, "minute": 1, "min": 1}
_UNIT = r"(?P<unit>hours?|hrs?|minutes?|mins?)"
_DURATION = re.compile(
    rf"(?P<low>{_NUM})(?:\s*(?:–|—|-|\bto\b)\s*(?P<high>{_NUM}))?\s*{_UNIT}\b",
    re.IGNORECASE,
)
_NOT_A_TOTAL = re.compile(
    r"\b(overnight|days?|weeks?|months?|per|each|varies|until)\b", re.IGNORECASE
)
_ADDS_TIME = re.compile(r"\bplus\b|\+|\bexcluding\b|\bnot including\b", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\(([^()]*)\)")
_STAGE_SPLIT = re.compile(r"</?br\s*/?>|\n|;|,|\+|\bplus\b", re.IGNORECASE)
_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*:\s*(.*)$", re.DOTALL)


def _number(text: str) -> float:
    text = text.replace(" ", "")
    if text[-1] in _FRACTIONS:
        whole = float(text[:-1]) if text[:-1] else 0.0
        return whole + _FRACTIONS[text[-1]]
    return float(text)


def _stage_minutes(text: str) -> int | None:
    total = 0.0
    found = False
    for match in _DURATION.finditer(text):
        found = True
        amount = _number(match["high"] or match["low"])
        unit = match["unit"].lower().rstrip("s")
        total += amount * _UNIT_MINUTES[unit]
    if not found:
        return None
    # A number left over after removing durations has no unit we can trust.
    if re.search(r"\d", _DURATION.sub(" ", text)):
        return None
    return round(total)


def parse_total_minutes(raw: str | None) -> Fact[int]:
    if raw is None or not raw.strip():
        return Fact.unknown()

    unknown = Fact[int].unknown(evidence=raw)
    text = html.unescape(raw).replace("\xa0", " ")

    for inner in _PARENTHETICAL.findall(text):
        if _ADDS_TIME.search(inner):
            return unknown
    text = _PARENTHETICAL.sub(" ", text)

    stages: list[tuple[str | None, int]] = []
    for chunk in _STAGE_SPLIT.split(text):
        if not chunk.strip():
            continue
        if re.match(r"\s*including\b", chunk, re.IGNORECASE):
            continue
        if _NOT_A_TOTAL.search(chunk):
            return unknown
        label_match = _LABEL.match(chunk)
        label, body = (label_match[1].lower(), label_match[2]) if label_match else (None, chunk)
        minutes = _stage_minutes(body)
        if minutes is None:
            return unknown
        stages.append((label, minutes))

    if not stages:
        return unknown
    for label, minutes in stages:
        if label == "total":
            return Fact(value=minutes, status="explicit", evidence=raw)
    if len(stages) == 1:
        label, minutes = stages[0]
        if label is not None:
            return unknown
        return Fact(value=minutes, status="explicit", evidence=raw)
    return Fact(value=sum(m for _, m in stages), status="derived", evidence=raw)
