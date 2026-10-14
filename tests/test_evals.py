"""Eval harness logic (SPEC §7), on recorded responses: no network, no key.

The HTTP runner talks to an httpx.MockTransport that replays bodies
recorded from production (tests/fixtures/evals/recorded_responses.json);
the extraction runner talks to the fake provider.
"""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from evals import provenance, report
from evals.checks import constraint_failures
from evals.extract import compare, run_extraction
from evals.rescore import rescore
from evals.run import RATE_LIMIT_WAIT_S, run_set
from evals.sets import SetError, check_disjoint, load_e2e_set, load_extraction_set
from recipe_qa.corpus import load_manifest, load_recipes
from recipe_qa.generation import PROMPT_VERSION
from recipe_qa.providers.base import ProviderUnavailable
from recipe_qa.providers.fake import FakeProvider
from recipe_qa.schemas import AskResponse, Extraction

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANIFEST = load_manifest(DATA / "manifest.json")
RECIPES = {r.id: r for r in load_recipes(DATA / "recipes.jsonl")}
RECORDED = json.loads(
    (Path(__file__).parent / "fixtures" / "evals" / "recorded_responses.json").read_text()
)
CARBONARA = RECORDED["carbonara_answer"]["body"]
OUT_OF_DOMAIN = RECORDED["capital_out_of_domain"]["body"]
SAFETY = RECORDED["baklava_safety"]["body"]
SERVER_SHA = "b8d3e3058063b4ea402070f4556b828d088d7efc"


# --- helpers -----------------------------------------------------------------


def refusal(reason: str) -> dict:
    """A recorded refusal re-labelled; citations dropped as SPEC §2.1 requires."""
    body = copy.deepcopy(OUT_OF_DOMAIN)
    body["refusal_reason"] = reason
    return body


def citing(*recipe_ids: str) -> dict:
    """The recorded carbonara answer, citing other corpus recipes."""
    body = copy.deepcopy(CARBONARA)
    by_id = {r.id: r for r in MANIFEST.recipes}
    body["citations"] = [{"title": by_id[i].title, "url": by_id[i].url} for i in recipe_ids]
    return body


def write_set(directory: Path, name: str, cases: list[dict], held_out: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"description": f"test set {name}", "held_out": held_out, "cases": cases}
    (directory / f"{name}.json").write_text(json.dumps(payload))


def write_jsonl(directory: Path, name: str, rows: list[dict]) -> None:
    (directory / "extraction").mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row) + "\n" for row in rows)
    (directory / "extraction" / f"{name}.jsonl").write_text(text)


def server(
    answers: dict[str, list[tuple[int, dict]]],
    health: dict | None = None,
) -> httpx.Client:
    """A fake instance: each question replays its recorded responses in order."""
    queues: dict[str, Iterator[tuple[int, dict]]] = {q: iter(r) for q, r in answers.items()}
    health = health or {
        "status": "ok",
        "version": SERVER_SHA,
        "corpus_version": MANIFEST.corpus_version,
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json=health)
        if request.url.path == "/schema":
            return httpx.Response(200, json=AskResponse.model_json_schema())
        if request.url.path == "/":
            return httpx.Response(200, text="<!doctype html><html></html>")
        question = json.loads(request.content)["question"]
        status, body = next(queues[question])
        headers = {"X-Request-ID": body["meta"]["request_id"]} if "meta" in body else {}
        return httpx.Response(status, json=body, headers=headers)

    return httpx.Client(base_url="http://eval.test", transport=httpx.MockTransport(handle))


def no_sleep(calls: list[float] | None = None) -> Callable[[float], None]:
    def sleep(seconds: float) -> None:
        if calls is not None:
            calls.append(seconds)

    return sleep


@pytest.fixture(autouse=True)
def quiet_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provenance asks git about the checkout; tests decide what git says."""

    def git(*args: str) -> tuple[int, str]:
        if args[:1] == ("rev-parse",):
            return 0, "0123456789abcdef0123456789abcdef01234567"
        if args[:1] == ("status",):
            return 0, ""
        if args[:2] == ("diff", "--quiet"):
            return (0, "") if args[2] == SERVER_SHA else (128, "")
        raise AssertionError(f"unexpected git call {args}")

    monkeypatch.setattr(provenance, "git", git)


def smoke_run(answers: dict[str, list[tuple[int, dict]]], **kwargs: object) -> dict:
    defaults: dict[str, object] = {"repeat": 2, "contract_only": False, "sleep": no_sleep()}
    defaults.update(kwargs)
    with server(answers) as client:
        return run_set(client, load_e2e_set("smoke"), **defaults)  # type: ignore[arg-type]


def recorded_smoke(repeat: int = 2) -> dict[str, list[tuple[int, dict]]]:
    return {
        RECORDED[name]["question"]: [(200, RECORDED[name]["body"])] * repeat
        for name in ("carbonara_answer", "capital_out_of_domain", "baklava_safety")
    }


# --- case sets ---------------------------------------------------------------


def test_smoke_set_is_a_named_tuning_set() -> None:
    smoke = load_e2e_set("smoke")
    assert smoke.name == "smoke"
    assert not smoke.held_out
    assert [c["id"] for c in smoke.cases] == ["smoke-howto", "smoke-out-of-domain", "smoke-safety"]
    assert smoke.describe()["sha256"] == smoke.sha256


def test_committed_sets_share_no_question_with_a_held_out_set() -> None:
    check_disjoint()


def test_a_question_shared_with_a_held_out_set_is_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, "golden", [{"id": "a", "question": "How do I make baklava?"}])
    write_set(
        tmp_path, "heldout", [{"id": "h", "question": "  how do I make  BAKLAVA? "}], held_out=True
    )
    with pytest.raises(SetError, match="heldout"):
        load_e2e_set("golden", tmp_path)


def test_an_extraction_question_shared_with_a_held_out_set_is_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, "heldout", [{"id": "h", "question": "Quick vegan soup?"}], held_out=True)
    label = {"in_domain": True, "safety_question": False, "diet": "vegan"}
    write_jsonl(tmp_path, "labels", [{"id": "x", "question": "quick vegan soup?", "expect": label}])
    with pytest.raises(SetError, match="heldout"):
        load_extraction_set("labels", tmp_path)


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    write_set(tmp_path, "dup", [{"id": "a", "question": "x?"}, {"id": "a", "question": "y?"}])
    with pytest.raises(SetError, match="duplicate"):
        load_e2e_set("dup", tmp_path)


def test_extraction_labels_must_be_a_valid_extraction(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path,
        "bad",
        [{"id": "x", "question": "q?", "expect": {"in_domain": True, "safety": False}}],
    )
    with pytest.raises(SetError, match="x"):
        load_extraction_set("bad", tmp_path)


def test_committed_extraction_smoke_set_loads() -> None:
    labels = load_extraction_set("smoke")
    assert labels.kind == "extraction"
    assert all(isinstance(c["expect"], Extraction) for c in labels.cases)


# --- end-to-end runner --------------------------------------------------------


def test_recorded_smoke_responses_pass_every_run() -> None:
    result = smoke_run(recorded_smoke())
    assert result["summary"] == {
        "cases": 3,
        "pass": 3,
        "fail": 0,
        "unstable": 0,
        "content": {"per_run": [3, 3], "required": 3, "fail": [], "unstable": []},
        "verdict": "pass",
    }
    assert result["repeat"] == 2
    assert all(len(case["runs"]) == 2 for case in result["cases"])
    assert result["endpoint_failures"] == []


def test_a_case_that_fails_in_one_run_is_unstable_and_fails_the_run() -> None:
    answers = recorded_smoke()
    question = RECORDED["capital_out_of_domain"]["question"]
    answers[question] = [(200, OUT_OF_DOMAIN), (200, refusal("out_of_corpus"))]
    result = smoke_run(answers)
    case = next(c for c in result["cases"] if c["id"] == "smoke-out-of-domain")
    assert case["verdict"] == "unstable"
    assert case["passes"] == "1/2"
    assert result["summary"]["unstable"] == 1
    assert result["summary"]["pass"] == 2
    assert result["summary"]["verdict"] == "fail"


def test_an_outcome_that_changes_between_runs_is_unstable_even_if_each_run_passes(
    tmp_path: Path,
) -> None:
    write_set(tmp_path, "loose", [{"id": "any", "question": "Anything with eggs?", "expect": {}}])
    answers = {"Anything with eggs?": [(200, CARBONARA), (200, refusal("out_of_corpus"))]}
    with server(answers) as client:
        result = run_set(
            client, load_e2e_set("loose", tmp_path), repeat=2, contract_only=False, sleep=no_sleep()
        )
    assert result["cases"][0]["verdict"] == "unstable"
    assert [r["outcome"] for r in result["cases"][0]["runs"]] == ["answer", "out_of_corpus"]


def test_a_case_failing_every_run_is_a_failure_not_unstable() -> None:
    answers = recorded_smoke()
    answers[RECORDED["carbonara_answer"]["question"]] = [(200, refusal("out_of_corpus"))] * 2
    result = smoke_run(answers)
    case = next(c for c in result["cases"] if c["id"] == "smoke-howto")
    assert case["verdict"] == "fail"
    assert result["summary"]["fail"] == 1


def test_a_case_failing_every_run_with_changing_outcomes_is_unstable() -> None:
    answers = recorded_smoke()
    question = RECORDED["capital_out_of_domain"]["question"]
    answers[question] = [(200, CARBONARA), (200, refusal("out_of_corpus"))]
    result = smoke_run(answers)
    case = next(c for c in result["cases"] if c["id"] == "smoke-out-of-domain")
    assert case["passes"] == "0/2"
    assert case["verdict"] == "unstable"
    assert result["summary"]["unstable"] == 1


def test_false_answers_false_refusals_and_wrong_reasons_are_counted_apart() -> None:
    answers = {
        # expected an answer, got a refusal: false refusal
        RECORDED["carbonara_answer"]["question"]: [(200, refusal("out_of_corpus"))] * 2,
        # expected out_of_domain, got an answer: false answer
        RECORDED["capital_out_of_domain"]["question"]: [(200, CARBONARA)] * 2,
        # expected safety, got out_of_corpus: refused, but for the wrong reason
        RECORDED["baklava_safety"]["question"]: [(200, refusal("out_of_corpus"))] * 2,
    }
    matrix = smoke_run(answers)["refusal_matrix"]
    assert matrix["runs"] == 6
    assert matrix["false_refusals"] == 2
    assert matrix["false_answers"] == 2
    assert matrix["wrong_refusal_reason"] == 2
    assert matrix["technical_errors"] == 0
    assert matrix["matrix"]["answer"] == {"out_of_corpus": 2}
    assert matrix["matrix"]["out_of_domain"] == {"answer": 2}
    assert matrix["matrix"]["safety"] == {"out_of_corpus": 2}


def test_a_technical_error_is_its_own_column_not_a_refusal() -> None:
    answers = recorded_smoke(repeat=1)
    error = {"error": {"code": "upstream_unavailable", "message": "down", "request_id": "r"}}
    answers[RECORDED["baklava_safety"]["question"]] = [(503, error)]
    result = smoke_run(answers, repeat=1)
    assert result["refusal_matrix"]["technical_errors"] == 1
    assert result["refusal_matrix"]["matrix"]["safety"] == {"error": 1}
    assert result["refusal_matrix"]["false_answers"] == 0


def test_a_parsed_response_that_breaks_the_contract_is_a_technical_error() -> None:
    answers = recorded_smoke(repeat=1)
    broken = copy.deepcopy(CARBONARA)
    broken["citations"].append({"title": "Elsewhere", "url": "https://example.org/recipe"})
    answers[RECORDED["carbonara_answer"]["question"]] = [(200, broken)]
    result = smoke_run(answers, repeat=1)
    run = next(c for c in result["cases"] if c["id"] == "smoke-howto")["runs"][0]
    assert run["outcome"] == "error"
    assert "citation outside the corpus: https://example.org/recipe" in run["failures"]
    assert result["refusal_matrix"]["technical_errors"] == 1
    assert result["refusal_matrix"]["matrix"]["answer"] == {"error": 1}


def test_rate_limited_requests_wait_and_retry_instead_of_failing() -> None:
    answers = recorded_smoke(repeat=1)
    limited = {"error": {"code": "rate_limited", "message": "slow down", "request_id": "r"}}
    question = RECORDED["carbonara_answer"]["question"]
    answers[question] = [(429, limited), (200, CARBONARA)]
    waits: list[float] = []
    result = smoke_run(answers, repeat=1, sleep=no_sleep(waits))
    case = next(c for c in result["cases"] if c["id"] == "smoke-howto")
    assert case["verdict"] == "pass"
    assert case["runs"][0]["rate_limit_waits"] == 1
    assert waits == [RATE_LIMIT_WAIT_S]


def test_contract_only_skips_expectations_and_the_refusal_matrix() -> None:
    answers = recorded_smoke(repeat=1)
    answers[RECORDED["capital_out_of_domain"]["question"]] = [(200, CARBONARA)]
    result = smoke_run(answers, repeat=1, contract_only=True)
    assert result["summary"]["verdict"] == "pass"
    assert result["summary"]["content"] is None
    assert result["refusal_matrix"] is None


def test_a_cited_recipe_must_satisfy_the_case_constraints(tmp_path: Path) -> None:
    cases = [
        {
            "id": "veg",
            "question": "A vegetarian pasta?",
            "expect": {"refused": False, "constraints": {"diet": "vegetarian"}},
        }
    ]
    write_set(tmp_path, "constrained", cases)
    answers = {"A vegetarian pasta?": [(200, citing("baklava-i", "spaghetti-alla-carbonara"))]}
    with server(answers) as client:
        result = run_set(
            client,
            load_e2e_set("constrained", tmp_path),
            repeat=1,
            contract_only=False,
            sleep=no_sleep(),
        )
    failures = result["cases"][0]["runs"][0]["failures"]
    assert len(failures) == 1
    assert "spaghetti-alla-carbonara" in failures[0]
    assert "vegetarian" in failures[0]


# --- content checks (SPEC §7.2, v0.6) -------------------------------------------

CARBONARA_QUESTION = RECORDED["carbonara_answer"]["question"]
ANSWERED = {"refused": False, "sources_any": ["spaghetti-alla-carbonara"]}


def content_run(tmp_path: Path, expect: dict, bodies: list[dict]) -> dict:
    """One case answered by each body in turn, one run per body."""
    write_set(tmp_path, "content", [{"id": "c", "question": CARBONARA_QUESTION, "expect": expect}])
    with server({CARBONARA_QUESTION: [(200, b) for b in bodies]}) as client:
        return run_set(
            client,
            load_e2e_set("content", tmp_path),
            repeat=len(bodies),
            contract_only=False,
            sleep=no_sleep(),
        )


def without(word: str) -> dict:
    body = copy.deepcopy(CARBONARA)
    body["answer"] = body["answer"].replace(word, "pork")
    return body


@pytest.mark.parametrize(
    ("content", "failure"),
    [
        ({"expected_facts": ["guanciale", "pecorino romano", "60 minutes|an hour"]}, None),
        ({"expected_facts": ["garlic"]}, "expected fact 'garlic' not in answer"),
        ({"forbidden_claims": [r"\bheavy cream\b"]}, None),
        # A mention of the other variant is still a mention.
        ({"forbidden_claims": ["lemon zest"]}, "forbidden claim 'lemon zest' in answer"),
        ({"sources_all": ["spaghetti-alla-carbonara", "carbonara-pasta"]}, None),
        ({"sources_all": ["spaghetti-alla-carbonara", "baklava-i"]}, "baklava-i not cited"),
    ],
)
def test_content_checks_read_the_answer_and_the_citations(
    tmp_path: Path, content: dict, failure: str | None
) -> None:
    run = content_run(tmp_path, {**ANSWERED, **content}, [CARBONARA])["cases"][0]["runs"][0]
    assert run["passed"] is True  # content never decides the hard checks
    assert run["content_passed"] is (failure is None)
    assert run["content_failures"] == ([] if failure is None else [failure])


def test_a_case_without_content_expectations_passes_content_only_with_the_right_outcome(
    tmp_path: Path,
) -> None:
    expect = {"refused": True, "refusal_reason": "out_of_domain"}
    runs = content_run(tmp_path, expect, [OUT_OF_DOMAIN, CARBONARA])["cases"][0]["runs"]
    assert [r["content_passed"] for r in runs] == [True, False]
    assert runs[1]["content_failures"] == ["outcome answer, expected out_of_domain"]


def test_content_is_counted_per_run_and_fails_a_run_whose_hard_checks_pass(
    tmp_path: Path,
) -> None:
    expect = {**ANSWERED, "expected_facts": ["guanciale"]}
    result = content_run(tmp_path, expect, [CARBONARA, without("guanciale")])
    case = result["cases"][0]
    assert case["verdict"] == "pass"
    assert case["content"] == {"verdict": "unstable", "passes": "1/2"}
    assert result["summary"]["content"] == {
        "per_run": [1, 0],
        "required": 1,
        "fail": [],
        "unstable": ["c"],
    }
    assert result["summary"]["verdict"] == "fail"


@pytest.mark.parametrize(("cases", "required"), [(15, 13), (3, 3), (1, 1), (30, 26)])
def test_content_needs_13_of_15_scaled_to_the_set(cases: int, required: int) -> None:
    assert report.content_required(cases) == required


def test_rescoring_a_recorded_report_counts_each_category_against_the_current_set(
    tmp_path: Path,
) -> None:
    recorded = content_run(tmp_path, ANSWERED, [CARBONARA, refusal("out_of_corpus")])
    stricter = {**ANSWERED, "expected_facts": ["garlic"]}
    write_set(tmp_path, "content", [{"id": "c", "question": CARBONARA_QUESTION, "expect": stricter}])

    table = rescore(recorded, load_e2e_set("content", tmp_path))

    assert table["categories"] == {
        "contract": {"passed": 2, "runs": 2},
        "refusal": {"passed": 1, "runs": 2},
        "sources": {"passed": 1, "runs": 2},
        "constraints": {"passed": 2, "runs": 2},
        "content": {"passed": 0, "runs": 2},
    }
    assert table["content"]["per_run"] == [0, 0]
    assert table["set_changed"] is True
    assert table["missing_cases"] == []


# --- constraint predicates (SPEC §4, checked against corpus metadata) ---------


@pytest.mark.parametrize(
    ("recipe_id", "constraints", "ok"),
    [
        # time: carbonara states 60 minutes; baklava-i states none
        ("spaghetti-alla-carbonara", {"max_total_minutes": 60}, False),
        ("spaghetti-alla-carbonara", {"max_total_minutes": 61}, True),
        (
            "spaghetti-alla-carbonara",
            {"max_total_minutes": 60, "time_limit_inclusive": True},
            True,
        ),
        ("baklava-i", {"max_total_minutes": 1000, "time_limit_inclusive": True}, False),
        # diet
        ("baklava-i", {"diet": "vegetarian"}, True),
        ("spaghetti-alla-carbonara", {"diet": "vegetarian"}, False),
        ("chunky-guacamole", {"diet": "vegan"}, True),
        ("baklava-i", {"diet": "vegan"}, False),
        ("balsamic-glaze", {"diet": "gluten_free"}, True),
        ("baklava-i", {"diet": "gluten_free"}, False),
        # include: word match on raw or normalized, plural tolerated, alternatives allowed
        ("spaghetti-alla-carbonara", {"must_include": ["egg"]}, True),
        ("kachumbari", {"must_include": ["tomato"]}, True),
        ("baklava-i", {"must_include": [["almond", "walnut"]]}, True),
        ("baklava-i", {"must_include": ["chicken"]}, False),
        # exclude: whole words only, so a case lists every nut it means
        ("baklava-i", {"must_exclude": ["nut"]}, True),
        ("baklava-i", {"must_exclude": [["nut", "walnut"]]}, False),
        # exclude: optional ingredients count as present
        ("kachumbari", {"must_exclude": ["cucumber"]}, False),
        # several constraints: all must hold
        ("kachumbari", {"diet": "vegan", "max_total_minutes": 20, "must_include": ["onion"]}, True),
        ("kachumbari", {"diet": "vegan", "max_total_minutes": 15}, False),
    ],
)
def test_constraint_predicates(recipe_id: str, constraints: dict, ok: bool) -> None:
    assert (constraint_failures(RECIPES[recipe_id], constraints) == []) is ok


def test_an_unknown_constraint_key_is_an_error_in_the_case() -> None:
    with pytest.raises(ValueError, match="max_minutes"):
        constraint_failures(RECIPES["baklava-i"], {"max_minutes": 30})


# --- provenance -----------------------------------------------------------------


def test_report_records_code_prompts_model_corpus_and_set() -> None:
    record = smoke_run(recorded_smoke(repeat=1), repeat=1)["provenance"]
    assert record["code"]["value"] == SERVER_SHA
    assert record["prompts"] == {
        "value": PROMPT_VERSION,
        "confirmed": True,
        "note": f"recipe_qa/prompts/ in this checkout is identical to build {SERVER_SHA}",
    }
    assert record["model"]["value"] == ["claude-haiku-5-5"]
    assert record["corpus"] == {
        "value": MANIFEST.corpus_version,
        "confirmed": True,
        "note": "matches data/manifest.json in this checkout",
    }
    assert record["set"]["name"] == "smoke"
    assert record["harness"] == {
        "git_sha": "0123456789abcdef0123456789abcdef01234567",
        "dirty": False,
    }


def test_versions_the_harness_cannot_confirm_are_marked_unconfirmed() -> None:
    health = {"status": "ok", "version": "dev", "corpus_version": "something-else"}
    with server(recorded_smoke(repeat=1), health=health) as client:
        record = run_set(
            client, load_e2e_set("smoke"), repeat=1, contract_only=False, sleep=no_sleep()
        )["provenance"]
    assert record["prompts"]["confirmed"] is False
    assert record["corpus"]["confirmed"] is False
    assert record["code"]["value"] == "dev"


def test_prompts_from_an_unknown_build_are_unconfirmed() -> None:
    health = {"status": "ok", "version": "f" * 40, "corpus_version": MANIFEST.corpus_version}
    with server(recorded_smoke(repeat=1), health=health) as client:
        record = run_set(
            client, load_e2e_set("smoke"), repeat=1, contract_only=False, sleep=no_sleep()
        )["provenance"]
    assert record["prompts"]["confirmed"] is False
    assert "not in this repository" in record["prompts"]["note"]


# --- reports and the held-out set -------------------------------------------------


def test_milestone_reports_are_committable_and_exploratory_reports_are_not() -> None:
    def ignored(path: str) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
        return result.returncode == 0

    assert ignored("evals/reports/20261014T000000Z-e2e-smoke.json")
    assert not ignored("evals/reports/milestones/stage-4a-e2e-smoke.json")


def test_a_milestone_report_is_never_overwritten(tmp_path: Path) -> None:
    path = report.write_report({"x": 1}, reports_dir=tmp_path, name="e2e-smoke", milestone="m1")
    assert path == tmp_path / "milestones" / "m1.json"
    with pytest.raises(FileExistsError):
        report.write_report({"x": 2}, reports_dir=tmp_path, name="e2e-smoke", milestone="m1")


def test_an_exploratory_report_gets_a_timestamped_name(tmp_path: Path) -> None:
    path = report.write_report({"x": 1}, reports_dir=tmp_path, name="e2e-smoke")
    assert path.parent == tmp_path
    assert path.name.endswith("-e2e-smoke.json")


def test_a_held_out_set_runs_only_as_a_milestone(tmp_path: Path) -> None:
    write_set(tmp_path, "heldout", [{"id": "h", "question": "q?"}], held_out=True)
    held_out = load_e2e_set("heldout", tmp_path)
    with pytest.raises(SetError, match="milestone"):
        report.held_out_guard(held_out, milestone=None, reports_dir=tmp_path)
    report.held_out_guard(held_out, milestone="final", reports_dir=tmp_path)


def test_a_held_out_set_is_evaluated_once(tmp_path: Path) -> None:
    write_set(tmp_path, "heldout", [{"id": "h", "question": "q?"}], held_out=True)
    held_out = load_e2e_set("heldout", tmp_path)
    first = {"provenance": {"set": held_out.describe()}}
    report.write_report(first, reports_dir=tmp_path, name="e2e-heldout", milestone="final")
    with pytest.raises(SetError, match=r"final\.json"):
        report.held_out_guard(held_out, milestone="final-again", reports_dir=tmp_path)


def test_a_tuning_set_needs_no_guard(tmp_path: Path) -> None:
    report.held_out_guard(load_e2e_set("smoke"), milestone=None, reports_dir=tmp_path)


# --- extraction-only runner -------------------------------------------------------


def labels(**overrides: object) -> Extraction:
    return Extraction(in_domain=True, safety_question=False).model_copy(update=overrides)


def test_compare_names_each_wrong_field_and_its_direction() -> None:
    expected = labels(
        safety_question=True,
        max_total_minutes=30,
        diet="vegan",
        must_include=["rice"],
        must_exclude=["egg"],
    )
    got = labels(
        in_domain=False,
        max_total_minutes=None,
        diet="vegetarian",
        must_include=["rice", "bean"],
        must_exclude=[],
    )
    errors = {(e["field"], e["kind"]) for e in compare(expected, got)}
    assert errors == {
        ("in_domain", "false_negative"),
        ("safety_question", "false_negative"),
        ("max_total_minutes", "missed"),
        ("diet", "wrong"),
        ("must_include", "spurious"),
        ("must_exclude", "missed"),
    }


def test_compare_ignores_order_and_case_in_ingredient_lists() -> None:
    expected = labels(must_include=["rice", "black bean"])
    assert compare(expected, labels(must_include=["Black Bean", "rice"])) == []


def test_compare_ignores_inclusiveness_when_no_time_limit_is_expected() -> None:
    assert compare(labels(), labels(time_limit_inclusive=True)) == []
    wrong = compare(
        labels(max_total_minutes=30), labels(max_total_minutes=30, time_limit_inclusive=True)
    )
    assert [(e["field"], e["kind"]) for e in wrong] == [("time_limit_inclusive", "wrong")]


def test_extraction_runner_reports_fields_and_stability(tmp_path: Path) -> None:
    rows = [
        {
            "id": "ok",
            "question": "Vegan soup?",
            "expect": {"in_domain": True, "safety_question": False, "diet": "vegan"},
        },
        {
            "id": "flaky",
            "question": "Is baklava nut-free?",
            "expect": {"in_domain": True, "safety_question": True},
        },
        {
            "id": "wrong",
            "question": "Capital of France?",
            "expect": {"in_domain": False, "safety_question": False},
        },
    ]
    write_jsonl(tmp_path, "labels", rows)
    flaky = iter([True, False])

    def extract(question: str) -> Extraction:
        if question == "Vegan soup?":
            return labels(diet="vegan")
        if question == "Is baklava nut-free?":
            return labels(safety_question=next(flaky))
        return labels()  # says in_domain for the capital of France

    provider = FakeProvider(extract=extract, model="recorded-model")
    result = asyncio.run(
        run_extraction(provider, load_extraction_set("labels", tmp_path), repeat=2)
    )
    verdicts = {c["id"]: c["verdict"] for c in result["cases"]}
    assert verdicts == {"ok": "pass", "flaky": "unstable", "wrong": "fail"}
    assert result["summary"]["verdict"] == "fail"
    assert result["fields"]["in_domain"] == {"runs": 6, "errors": {"false_positive": 2}}
    assert result["fields"]["safety_question"] == {"runs": 6, "errors": {"false_negative": 1}}
    assert result["fields"]["diet"] == {"runs": 6, "errors": {}}
    assert result["provenance"]["model"]["value"] == "recorded-model"
    assert result["provenance"]["prompts"]["value"] == PROMPT_VERSION
    assert result["provenance"]["prompts"]["confirmed"] is True
    assert result["provenance"]["set"]["kind"] == "extraction"


def test_extraction_runner_records_a_provider_error_as_a_failed_run(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path,
        "labels",
        [{"id": "x", "question": "Soup?", "expect": {"in_domain": True, "safety_question": False}}],
    )

    def down(question: str) -> Extraction:
        raise ProviderUnavailable("timeout")

    provider = FakeProvider(extract=down)
    result = asyncio.run(
        run_extraction(provider, load_extraction_set("labels", tmp_path), repeat=1)
    )
    run = result["cases"][0]["runs"][0]
    assert run["passed"] is False
    assert run["error"] == "ProviderUnavailable: timeout"
    assert result["cases"][0]["verdict"] == "fail"
