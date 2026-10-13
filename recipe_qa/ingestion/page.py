"""Whole-page parsing: wikitext → summary fields, ingredients, steps, sections.

Structure rules (derived from scanning ~970 candidate pages):

- Summary fields come from {{Recipe summary}} / {{recipesummary}}; parameter
  names are case-insensitive. `time` keeps its wikitext (line breaks
  separate stages, see timeparse); other fields are plain text.
- Categories are the [[Category:...]] links of the pinned revision, so they
  are reproducible offline (the live API reports current categories).
- Level-2 headings split the page. The ingredients section is "Ingredients".
  The procedure section is the first "Procedure*", else the first
  "Directions"/"Instructions"/"Method", else the first "Preparation".
- Inside ingredients, a level-3+ heading or a ";term" line starts a group.
  A top-level item ending with ":" labels its nested items ("Optional:")
  and is not itself an ingredient.
- Inside the procedure, list items are steps; without list items, each
  paragraph is a step. A sub-heading prefixes its steps ("Syrup: ...").
- Every other section is kept as plain text, except references and
  navigation. Text before the first heading becomes "description".
"""

import re
from dataclasses import dataclass

import mwparserfromhell as mwp

from recipe_qa.corpus import Ingredient
from recipe_qa.ingestion.ingredients import OPTIONAL_MARKER, parse_ingredient_line
from recipe_qa.ingestion.wikitext import to_plain

_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")
_LIST_ITEM = re.compile(r"^([*#]+):?\s*(.*)$")
_CATEGORY = re.compile(r"\[\[\s*category\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]", re.IGNORECASE)
_SUMMARY_TEMPLATES = {"recipe summary", "recipesummary"}
_INGREDIENTS = re.compile(r"^ingredients?:?$", re.IGNORECASE)
_PROCEDURE_PRIORITY = [
    re.compile(r"^procedure\b", re.IGNORECASE),
    re.compile(r"^(directions|instructions|method)\b", re.IGNORECASE),
    re.compile(r"^preparation\b", re.IGNORECASE),
]
_DROPPED_SECTIONS = {
    "references",
    "reference",
    "see also",
    "external links",
    "gallery",
    "related recipes",
}
_REF_OR_COMMENT = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", re.DOTALL)
_BREADCRUMB = re.compile(r"^\s*\{\{\s*recipe\s*\}\}", re.IGNORECASE)
_MAGIC_WORD = re.compile(r"__[A-Z]+__")


@dataclass(frozen=True)
class ParsedPage:
    summary_time: str | None
    summary_servings: str | None
    summary_difficulty: str | None
    categories: list[str]
    ingredients: list[Ingredient]
    steps: list[str]
    sections: dict[str, str]


@dataclass
class _Section:
    title: str | None
    lines: list[str]


def _summary(wikitext: str) -> dict[str, str]:
    for template in mwp.parse(wikitext).filter_templates(recursive=False):
        if str(template.name).strip().lower() in _SUMMARY_TEMPLATES:
            return {
                str(p.name).strip().lower(): _REF_OR_COMMENT.sub("", str(p.value)).strip()
                for p in template.params
            }
    return {}


def _categories(wikitext: str) -> list[str]:
    seen: dict[str, None] = {}
    for name in _CATEGORY.findall(wikitext):
        seen.setdefault(" ".join(name.split()), None)
    return list(seen)


def _split_sections(wikitext: str) -> list[_Section]:
    sections = [_Section(title=None, lines=[])]
    for line in wikitext.splitlines():
        heading = _HEADING.match(line)
        if heading and len(heading[1]) == 2:
            sections.append(_Section(title=to_plain(heading[2]), lines=[]))
        else:
            sections[-1].lines.append(line)
    return sections


def _ingredients(lines: list[str]) -> list[Ingredient]:
    result: list[Ingredient] = []
    group: str | None = None
    label: str | None = None
    label_optional = False
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            group, label = to_plain(heading[2]), None
            continue
        if line.startswith(";"):
            group, label = to_plain(line[1:]).rstrip(":").strip(), None
            continue
        item = _LIST_ITEM.match(line)
        if not item or not item[2].strip():
            continue
        depth, body = len(item[1]), item[2].strip()
        if depth == 1:
            plain = to_plain(body)
            if plain.endswith(":"):
                label = plain.rstrip(":").strip()
                label_optional = bool(OPTIONAL_MARKER.search(plain))
                continue
            label = None
            result.append(parse_ingredient_line(body, group))
        else:
            ingredient = parse_ingredient_line(body, label or group)
            if label is not None and label_optional:
                ingredient = ingredient.model_copy(update={"optional": True})
            result.append(ingredient)
    return result


def _steps(lines: list[str]) -> list[str]:
    has_list = any(_LIST_ITEM.match(line) for line in lines)
    steps: list[str] = []
    group: str | None = None
    paragraph: list[str] = []

    def add(text: str) -> None:
        plain = to_plain(text)
        if plain:
            steps.append(f"{group}: {plain}" if group else plain)

    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            if paragraph:
                add(" ".join(paragraph))
                paragraph = []
            group = to_plain(heading[2])
            continue
        if has_list:
            item = _LIST_ITEM.match(line)
            if item:
                add(item[2])
        elif line.strip():
            paragraph.append(line)
        elif paragraph:
            add(" ".join(paragraph))
            paragraph = []
    if paragraph:
        add(" ".join(paragraph))
    return steps


def _section_text(lines: list[str]) -> str:
    text = "\n".join(line for line in lines if not _BREADCRUMB.match(line))
    text = _MAGIC_WORD.sub("", text)
    text = re.sub(r"(?m)^[*#:;]+\s*", "", text)
    return to_plain(text)


def _pick_procedure(sections: list[_Section]) -> _Section | None:
    for pattern in _PROCEDURE_PRIORITY:
        for section in sections:
            if section.title and pattern.match(section.title):
                return section
    return None


def parse_page(wikitext: str) -> ParsedPage:
    summary = _summary(wikitext)
    sections = _split_sections(wikitext)
    ingredients_section = next(
        (s for s in sections if s.title and _INGREDIENTS.match(s.title)), None
    )
    procedure_section = _pick_procedure(sections)

    other: dict[str, str] = {}
    for section in sections:
        if section is ingredients_section or section is procedure_section:
            continue
        key = "description" if section.title is None else section.title.lower()
        if key in _DROPPED_SECTIONS:
            continue
        text = _section_text(section.lines)
        if text:
            other[key] = f"{other[key]}\n{text}" if key in other else text

    def plain_field(name: str) -> str | None:
        value = to_plain(summary.get(name, ""))
        return value or None

    return ParsedPage(
        summary_time=summary.get("time") or None,
        summary_servings=plain_field("servings"),
        summary_difficulty=plain_field("difficulty"),
        categories=_categories(wikitext),
        ingredients=_ingredients(ingredients_section.lines) if ingredients_section else [],
        steps=_steps(procedure_section.lines) if procedure_section else [],
        sections=other,
    )
