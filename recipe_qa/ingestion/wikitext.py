"""Wikitext → visible plain text (mwparserfromhell).

Only what a reader sees on the page survives: link labels, text inside
formatting tags, the value and unit of {{convert}}. References, comments,
files, categories and other templates are dropped.
"""

import html
import re

import mwparserfromhell as mwp
from mwparserfromhell.nodes import (
    Argument,
    Comment,
    ExternalLink,
    Heading,
    HTMLEntity,
    Tag,
    Template,
    Text,
    Wikilink,
)
from mwparserfromhell.wikicode import Wikicode

_HIDDEN_LINK_NAMESPACES = {"file", "image", "category", "media"}
_DROPPED_TAGS = {"ref", "references", "gallery", "nowiki"}


def _link_label(link: Wikilink) -> str:
    if link.text is not None:
        return _render(link.text)
    target = str(link.title).strip()
    # [[Cookbook:Salt]] displays as the page name; show the name only.
    return target.split(":", 1)[-1] if ":" in target else target


def _template(node: Template) -> str:
    name = str(node.name).strip().lower()
    if name == "convert" and node.has("1") and node.has("2"):
        return f"{node.get('1').value.strip()} {node.get('2').value.strip()}"
    if name == "frac" and node.has("1"):
        numbers = [str(node.get(k).value).strip() for k in ("1", "2", "3") if node.has(k)]
        return " ".join([*numbers[:-2], "/".join(numbers[-2:])]) if len(numbers) > 1 else ""
    return ""


def _render(code: Wikicode) -> str:
    parts: list[str] = []
    for node in code.nodes:
        if isinstance(node, Text):
            parts.append(str(node))
        elif isinstance(node, HTMLEntity):
            parts.append(node.normalize())
        elif isinstance(node, Wikilink):
            namespace = str(node.title).split(":", 1)[0].strip().lower()
            if namespace not in _HIDDEN_LINK_NAMESPACES:
                parts.append(_link_label(node))
        elif isinstance(node, ExternalLink):
            if node.title is not None:
                parts.append(_render(node.title))
        elif isinstance(node, Template):
            parts.append(_template(node))
        elif isinstance(node, Tag):
            tag = str(node.tag).strip().lower()
            if tag == "br":
                parts.append(" ")
            elif tag not in _DROPPED_TAGS and node.contents is not None:
                parts.append(_render(node.contents))
        elif isinstance(node, Heading):
            parts.append(_render(node.title))
        elif isinstance(node, Comment | Argument):
            continue
    return "".join(parts)


def to_plain(wikitext: str) -> str:
    text = html.unescape(_render(mwp.parse(wikitext)))
    return re.sub(r"\s+", " ", text).strip()
