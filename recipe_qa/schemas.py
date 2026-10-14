"""Wire contract of POST /ask (SPEC §2) and the structured outputs of the
two model calls (SPEC §3, ADR 002).

Invariants I1, I2 and I4 live in AskResponse's validator, so a response
that breaks them cannot be constructed, let alone sent. I3 (citations come
from the manifest) is guaranteed by the service, which is the only place
that builds citations.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

RefusalReason = Literal["out_of_corpus", "out_of_domain", "safety"]
Diet = Literal["vegetarian", "vegan", "gluten_free"]

MAX_QUESTION_CODE_POINTS = 1000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskRequest(StrictModel):
    question: str

    @model_validator(mode="after")
    def _question_length(self) -> Self:
        self.question = self.question.strip()
        if not 1 <= len(self.question) <= MAX_QUESTION_CODE_POINTS:
            raise ValueError(
                f"question must be 1-{MAX_QUESTION_CODE_POINTS} characters after trimming"
            )
        return self


class Citation(StrictModel):
    title: str
    url: str


class Meta(StrictModel):
    request_id: str
    model: str
    corpus_version: str
    latency_ms: int = Field(ge=0)


class AskResponse(StrictModel):
    answer: str | None
    citations: list[Citation]
    refused: bool
    refusal_reason: RefusalReason | None
    refusal_message: str | None
    meta: Meta

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if self.refused:
            # I1
            if self.answer is not None:
                raise ValueError("a refusal has answer == null")
            if self.refusal_reason is None or not (self.refusal_message or "").strip():
                raise ValueError("a refusal has a reason and a message")
            # SPEC §2.1 citations column
            limit = 3 if self.refusal_reason == "safety" else 0
            if len(self.citations) > limit:
                raise ValueError(f"{self.refusal_reason} allows at most {limit} citations")
        else:
            if not (self.answer or "").strip():
                raise ValueError("an answer is a non-empty string")
            if self.refusal_reason is not None or self.refusal_message is not None:
                raise ValueError("an answer has no refusal reason or message")
            # I2
            if not 1 <= len(self.citations) <= 3:
                raise ValueError("an answer cites 1-3 recipes")
        return self


class ErrorDetail(StrictModel):
    code: Literal[
        "invalid_request",
        "rate_limited",
        "upstream_invalid",
        "upstream_unavailable",
        "internal_error",
    ]
    message: str
    request_id: str


class ErrorResponse(StrictModel):
    error: ErrorDetail


# --- model outputs (SPEC §3 steps 3 and 7) ----------------------------------


# Docstrings and field descriptions below are sent to the model as part of
# the JSON schema, so they are written for it.


class Extraction(StrictModel):
    """What the question asks for. Never an answer to it."""

    # The service gates on in_domain and safety_question; the constraint
    # fields are applied by code (recipe_qa/constraints.py), never by the model.

    in_domain: bool = Field(description="True if the question is about food, cooking or recipes.")
    safety_question: bool = Field(
        description=(
            "True if the question asks for an allergen or food-safety guarantee: "
            "'-free', 'safe for', allergy, allergic, intolerant, celiac, or whether "
            "someone can eat it."
        )
    )
    max_total_minutes: int | None = Field(
        default=None, description="Upper time limit in minutes, or null if none is asked."
    )
    time_limit_inclusive: bool = Field(
        default=False,
        description="True for 'within / at most / N minutes or less'; false for 'under N'.",
    )
    diet: Diet | None = None
    must_include: list[str] = Field(
        default_factory=list, description="Ingredients the recipe must use, singular, lower case."
    )
    must_exclude: list[str] = Field(
        default_factory=list,
        description="Ingredients the recipe must not contain, singular, lower case.",
    )


class Generation(StrictModel):
    """An answer grounded in the given recipes, or a statement that they cannot answer."""

    answerable: bool = Field(
        description="False if the given recipes do not contain what the question asks."
    )
    answer: str | None = Field(
        description="Plain-text answer grounded only in the given recipes; null if unanswerable."
    )
    source_ids: list[str] = Field(
        description="IDs of the given recipes the answer is based on; empty if unanswerable."
    )
