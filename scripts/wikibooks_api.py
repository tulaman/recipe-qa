"""Minimal, polite MediaWiki API client for en.wikibooks.org (stdlib only).

Follows the Wikimedia API etiquette: descriptive User-Agent with contact,
sequential requests, `maxlag`, batched titles (≤ 50 per request) and
retries with backoff on lag or transient errors.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "recipe-qa-ingest/0.1 (https://github.com/tulaman/recipe-qa; ilya.lityuga@gmail.com)"
BATCH = 50
COOKBOOK_NS = 102


@dataclass(frozen=True)
class Revision:
    title: str
    page_id: int
    revid: int
    timestamp: str
    wikitext: str


def call(**params: Any) -> dict[str, Any]:
    params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(5):
        request = urllib.request.Request(API, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2**attempt)
            continue
        error = data.get("error", {})
        if error.get("code") == "maxlag":
            time.sleep(5 * (attempt + 1))
            continue
        if error:
            raise RuntimeError(f"MediaWiki API error: {error}")
        return data
    raise RuntimeError(f"MediaWiki API unavailable after retries: {params.get('action')}")


def category_members(category: str) -> list[str]:
    titles: list[str] = []
    cont: dict[str, str] = {}
    while True:
        data = call(
            action="query",
            list="categorymembers",
            cmtitle=category,
            cmnamespace=COOKBOOK_NS,
            cmtype="page",
            cmlimit="500",
            **cont,
        )
        titles += [m["title"] for m in data["query"]["categorymembers"]]
        if "continue" not in data:
            return titles
        cont = {"cmcontinue": data["continue"]["cmcontinue"]}


def _revisions(data: dict[str, Any]) -> Iterator[Revision]:
    for page in data["query"].get("pages", []):
        if page.get("missing") or "revisions" not in page:
            continue
        rev = page["revisions"][0]
        yield Revision(
            title=page["title"],
            page_id=page["pageid"],
            revid=rev["revid"],
            timestamp=rev["timestamp"],
            wikitext=rev["slots"]["main"]["content"],
        )


def latest_revisions(titles: list[str]) -> dict[str, Revision]:
    """Current revision of each title; redirects are not followed."""
    result: dict[str, Revision] = {}
    for start in range(0, len(titles), BATCH):
        data = call(
            action="query",
            prop="revisions",
            titles="|".join(titles[start : start + BATCH]),
            rvprop="ids|timestamp|content",
            rvslots="main",
        )
        result.update((r.title, r) for r in _revisions(data))
    return result


def revisions_by_id(revids: list[int]) -> dict[int, Revision]:
    """Exact pinned revisions."""
    result: dict[int, Revision] = {}
    for start in range(0, len(revids), BATCH):
        data = call(
            action="query",
            prop="revisions",
            revids="|".join(str(r) for r in revids[start : start + BATCH]),
            rvprop="ids|timestamp|content",
            rvslots="main",
        )
        result.update((r.revid, r) for r in _revisions(data))
    return result


def canonical_urls(titles: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for start in range(0, len(titles), BATCH):
        data = call(
            action="query",
            prop="info",
            inprop="url",
            titles="|".join(titles[start : start + BATCH]),
        )
        result.update((p["title"], p["canonicalurl"]) for p in data["query"]["pages"])
    return result
