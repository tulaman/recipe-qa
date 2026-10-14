"""HTTP contract of POST /ask and the other endpoints (SPEC §2, §3, §10).

Runs on the fake provider: no key, no network.
"""

import json
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recipe_qa.api import create_app
from recipe_qa.config import Settings
from recipe_qa.corpus import Recipe, load_manifest
from recipe_qa.providers.base import ProviderInvalidOutput, ProviderUnavailable
from recipe_qa.providers.fake import FakeProvider
from recipe_qa.schemas import AskResponse, Extraction, Generation

DATA = Path(__file__).resolve().parent.parent / "data"
MANIFEST = load_manifest(DATA / "manifest.json")
URLS = {r.id: r.url for r in MANIFEST.recipes}


def settings(**overrides: object) -> Settings:
    return Settings(version="test-sha", data_dir=DATA, web_dist=None).model_copy(update=overrides)


def client_for(provider: FakeProvider | None = None, **overrides: object) -> TestClient:
    app = create_app(settings(**overrides), provider or FakeProvider())
    return TestClient(app, raise_server_exceptions=False)


def ask(client: TestClient, question: object) -> tuple[int, dict]:
    response = client.post("/ask", json={"question": question})
    body = response.json()
    request_id = body["meta"]["request_id"] if "meta" in body else body["error"]["request_id"]
    assert response.headers["X-Request-ID"] == request_id
    uuid.UUID(request_id)
    return response.status_code, body


def extraction(**overrides: object) -> Extraction:
    return Extraction(in_domain=True, safety_question=False).model_copy(update=overrides)


def citing(*recipe_ids: str, answer: str = "Do this.") -> Callable[..., Generation]:
    def generate(question: str, ex: Extraction, recipes: list[Recipe]) -> Generation:
        return Generation(answerable=True, answer=answer, source_ids=list(recipe_ids))

    return generate


# --- answers ---------------------------------------------------------------


def test_answer_cites_canonical_title_and_url_from_the_manifest() -> None:
    provider = FakeProvider(generate=citing("spaghetti-alla-carbonara"))
    status, body = ask(client_for(provider), "How do I make spaghetti alla carbonara?")

    assert status == 200
    parsed = AskResponse.model_validate(body)
    assert parsed.refused is False
    assert parsed.answer == "Do this."
    assert [c.model_dump() for c in parsed.citations] == [
        {"title": "Spaghetti alla Carbonara", "url": URLS["spaghetti-alla-carbonara"]}
    ]
    assert parsed.meta.model == provider.model
    assert parsed.meta.corpus_version == MANIFEST.corpus_version
    assert parsed.meta.latency_ms >= 0


def test_generation_sees_at_most_three_ranked_recipes() -> None:
    provider = FakeProvider(generate=citing("baklava-i"))
    status, _ = ask(client_for(provider), "How do I make baklava?")

    assert status == 200
    (call,) = provider.generate_calls
    assert 1 <= len(call) <= 3
    assert {"baklava-i", "baklava-ii"} <= set(call)


def test_duplicate_source_ids_are_cited_once() -> None:
    provider = FakeProvider(generate=citing("baklava-i", "baklava-i"))
    status, body = ask(client_for(provider), "How do I make baklava?")
    assert status == 200
    assert len(body["citations"]) == 1


def test_a_1000_code_point_question_is_accepted() -> None:
    status, _ = ask(client_for(), "é" * 1000)
    assert status == 200


# --- refusals --------------------------------------------------------------


def test_out_of_domain_refuses_without_citations_or_generation() -> None:
    provider = FakeProvider(extract=lambda q: extraction(in_domain=False))
    status, body = ask(client_for(provider), "What is the capital of France?")

    assert status == 200
    assert body["refused"] is True
    assert body["refusal_reason"] == "out_of_domain"
    assert body["answer"] is None
    assert body["refusal_message"]
    assert body["citations"] == []
    assert provider.generate_calls == []


def test_safety_refuses_but_cites_the_matched_recipe() -> None:
    provider = FakeProvider(extract=lambda q: extraction(safety_question=True))
    status, body = ask(client_for(provider), "Is the baklava nut-free?")

    assert status == 200
    assert body["refused"] is True
    assert body["refusal_reason"] == "safety"
    assert body["answer"] is None
    assert 1 <= len(body["citations"]) <= 3
    assert URLS["baklava-i"] in {c["url"] for c in body["citations"]}
    assert provider.generate_calls == []


def test_unanswerable_from_the_recipes_is_out_of_corpus() -> None:
    provider = FakeProvider(
        generate=lambda q, ex, rs: Generation(answerable=False, answer=None, source_ids=[])
    )
    status, body = ask(client_for(provider), "How many calories in the rice pudding?")

    assert status == 200
    assert body["refusal_reason"] == "out_of_corpus"
    assert body["citations"] == []


def test_no_matching_recipe_is_out_of_corpus_without_generation() -> None:
    provider = FakeProvider()
    status, body = ask(client_for(provider), "xylophone quantum")

    assert status == 200
    assert body["refusal_reason"] == "out_of_corpus"
    assert provider.generate_calls == []


# --- invalid model output: retry once, then 502 ----------------------------


def test_citation_outside_the_candidates_is_retried_once() -> None:
    outcomes = iter([citing("rice-pudding"), citing("baklava-i")])
    provider = FakeProvider(generate=lambda q, ex, rs: next(outcomes)(q, ex, rs))
    status, body = ask(client_for(provider), "How do I make baklava?")

    assert status == 200
    assert len(provider.generate_calls) == 2
    assert body["citations"][0]["url"] == URLS["baklava-i"]


@pytest.mark.parametrize(
    "bad",
    [
        Generation(answerable=True, answer="x", source_ids=["not-a-recipe"]),
        Generation(answerable=True, answer="x", source_ids=[]),
        Generation(answerable=True, answer="  ", source_ids=["baklava-i"]),
        Generation(answerable=True, answer=None, source_ids=["baklava-i"]),
    ],
)
def test_persistently_invalid_generation_is_502(bad: Generation) -> None:
    provider = FakeProvider(generate=lambda q, ex, rs: bad)
    status, body = ask(client_for(provider), "How do I make baklava?")

    assert status == 502
    assert body["error"]["code"] == "upstream_invalid"
    assert len(provider.generate_calls) == 2


def test_unparseable_extraction_is_retried_then_502() -> None:
    def broken(q: str) -> Extraction:
        raise ProviderInvalidOutput("not json")

    provider = FakeProvider(extract=broken)
    status, body = ask(client_for(provider), "How do I make baklava?")

    assert status == 502
    assert body["error"]["code"] == "upstream_invalid"
    assert provider.extract_calls == 2


# --- technical failures are never refusals ---------------------------------


def test_provider_outage_is_503_not_a_refusal() -> None:
    def down(q: str) -> Extraction:
        raise ProviderUnavailable("timeout")

    status, body = ask(client_for(FakeProvider(extract=down)), "How do I make baklava?")
    assert status == 503
    assert body["error"]["code"] == "upstream_unavailable"
    assert "refused" not in body


def test_request_deadline_is_503() -> None:
    provider = FakeProvider(delay_s=0.5)
    status, body = ask(client_for(provider, request_deadline_s=0.1), "How do I make baklava?")
    assert status == 503
    assert body["error"]["code"] == "upstream_unavailable"


def test_unexpected_exception_is_500() -> None:
    def boom(q: str) -> Extraction:
        raise RuntimeError("bug")

    status, body = ask(client_for(FakeProvider(extract=boom)), "How do I make baklava?")
    assert status == 500
    assert body["error"]["code"] == "internal_error"


# --- request validation (422) ----------------------------------------------


@pytest.mark.parametrize("question", ["", "   \n\t", "x" * 1001, 42, None, ["baklava"]])
def test_invalid_question_is_422(question: object) -> None:
    provider = FakeProvider()
    status, body = ask(client_for(provider), question)
    assert status == 422
    assert body["error"]["code"] == "invalid_request"
    assert provider.extract_calls == 0


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        ("not json", "application/json"),
        ('{"question": "baklava"}', "text/plain"),
        ("{}", "application/json"),
        ('["baklava"]', "application/json"),
        (json.dumps({"question": "baklava", "pad": "x" * 9000}), "application/json"),
    ],
)
def test_malformed_body_is_422(content: str, content_type: str) -> None:
    response = client_for().post("/ask", content=content, headers={"Content-Type": content_type})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]


def test_rate_limit_per_client_is_429() -> None:
    client = client_for(rate_limit_per_minute=2)
    assert ask(client, "baklava")[0] == 200
    assert ask(client, "baklava")[0] == 200
    status, body = ask(client, "baklava")
    assert status == 429
    assert body["error"]["code"] == "rate_limited"


# --- other endpoints -------------------------------------------------------


def test_healthz_reports_build_and_corpus_without_calling_the_provider() -> None:
    provider = FakeProvider()
    response = client_for(provider).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "test-sha",
        "corpus_version": MANIFEST.corpus_version,
    }
    assert provider.extract_calls == 0


def test_schema_endpoint_publishes_the_response_schema() -> None:
    response = client_for().get("/schema")
    assert response.status_code == 200
    assert response.json() == AskResponse.model_json_schema()


def test_ui_is_served_from_the_built_assets(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Recipe Q&A</title>")
    response = client_for(web_dist=tmp_path).get("/")
    assert response.status_code == 200
    assert "Recipe Q&amp;A" in response.text or "Recipe Q&A" in response.text


def test_the_alias_table_comes_from_the_configured_data_dir(tmp_path: Path) -> None:
    # A data directory is one snapshot: recipes, manifest and aliases together.
    for name in ("recipes.jsonl", "manifest.json"):
        (tmp_path / name).write_bytes((DATA / name).read_bytes())
    (tmp_path / "aliases.json").write_text(
        json.dumps({"description": "none", "aliases": {}, "exclude_only": {}})
    )
    garbanzo = FakeProvider(extract=lambda q: extraction(must_include=["garbanzo bean"]))
    question = "What can I cook with garbanzo beans?"

    status, body = ask(client_for(garbanzo), question)
    assert (status, body["refused"]) == (200, False)  # the committed table maps it to chickpea

    status, body = ask(client_for(garbanzo, data_dir=tmp_path), question)
    assert (status, body["refusal_reason"]) == (200, "out_of_corpus")
