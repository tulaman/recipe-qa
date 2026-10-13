"""Response invariants I1, I2, I4 are enforced by the model itself (SPEC §2.1)."""

import pytest
from pydantic import ValidationError

from recipe_qa.schemas import AskResponse, Citation, Meta

META = Meta(request_id="r", model="m", corpus_version="v", latency_ms=1)
CITE = Citation(title="Baklava I", url="https://en.wikibooks.org/wiki/Cookbook:Baklava_I")


def answered(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "answer": "Layer the filo.",
        "citations": [CITE],
        "refused": False,
        "refusal_reason": None,
        "refusal_message": None,
        "meta": META,
    }
    return data | overrides


def refused(reason: str, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "answer": None,
        "citations": [],
        "refused": True,
        "refusal_reason": reason,
        "refusal_message": "Sorry.",
        "meta": META,
    }
    return data | overrides


def test_valid_answer_and_refusals() -> None:
    AskResponse.model_validate(answered())
    AskResponse.model_validate(answered(citations=[CITE, CITE, CITE]))
    AskResponse.model_validate(refused("out_of_corpus"))
    AskResponse.model_validate(refused("out_of_domain"))
    AskResponse.model_validate(refused("safety", citations=[CITE]))


@pytest.mark.parametrize(
    "data",
    [
        # I1: every refusal field moves together
        answered(answer=None),
        answered(answer="   "),
        answered(refusal_reason="safety"),
        answered(refusal_message="Sorry."),
        refused("safety", answer="It is fine."),
        refused("safety", refusal_message=None),
        refused("safety", refusal_message=""),
        refused("safety", refusal_reason=None),
        # I2: an answer cites 1-3 recipes
        answered(citations=[]),
        answered(citations=[CITE, CITE, CITE, CITE]),
        # SPEC §2.1: no citations on out_of_corpus / out_of_domain, at most 3 on safety
        refused("out_of_corpus", citations=[CITE]),
        refused("out_of_domain", citations=[CITE]),
        refused("safety", citations=[CITE, CITE, CITE, CITE]),
        # I4: closed set of reasons
        refused("too_hard"),
    ],
)
def test_invariant_violations_are_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AskResponse.model_validate(data)


def test_json_schema_lists_the_contract_fields() -> None:
    schema = AskResponse.model_json_schema()
    assert set(schema["properties"]) == {
        "answer",
        "citations",
        "refused",
        "refusal_reason",
        "refusal_message",
        "meta",
    }
