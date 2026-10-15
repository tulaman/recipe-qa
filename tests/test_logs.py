"""Log lines carry their own level, one JSON object per line, on stdout.

Railway turns every stderr line into level "error" and reads `level` from
a single-line JSON log (stage 6 found every request trace tagged error).
"""

import io
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evals.stages import read_traces
from recipe_qa.api import create_app
from recipe_qa.config import Settings
from recipe_qa.logs import JsonFormatter, configure
from recipe_qa.providers.base import ProviderUnavailable
from recipe_qa.providers.fake import FakeProvider

DATA = Path(__file__).resolve().parent.parent / "data"
LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "recipe_qa.request")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    saved = {
        name: (lg.handlers[:], lg.level, lg.propagate)
        for name in LOGGERS
        for lg in [logging.getLogger(name)]
    }
    yield
    for name, (handlers, level, propagate) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate


def lines(stream: io.StringIO) -> list[dict]:
    text = stream.getvalue()
    return [json.loads(line) for line in text.splitlines() if line]


def test_a_plain_message_becomes_one_json_line_with_its_level() -> None:
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP %s", ("200",), None)
    line = JsonFormatter().format(record)
    assert "\n" not in line
    assert json.loads(line) == {"level": "info", "logger": "httpx", "message": "HTTP 200"}


def test_levels_use_railways_names() -> None:
    def level(n: int) -> str:
        record = logging.LogRecord("x", n, __file__, 1, "m", (), None)
        return json.loads(JsonFormatter().format(record))["level"]

    assert [level(n) for n in (10, 20, 30, 40, 50)] == [
        "debug",
        "info",
        "warn",
        "error",
        "error",
    ]


def test_an_exception_stays_on_one_line() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "recipe_qa.request", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    line = JsonFormatter().format(record)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["level"] == "error"
    assert "ValueError: boom" in parsed["exception"]


def test_configure_sends_uvicorn_and_app_logs_to_one_json_stream(restore_logging: None) -> None:
    stream = io.StringIO()
    configure(stream)
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    logging.getLogger("httpx").info("HTTP Request: POST")
    assert [(r["logger"], r["level"]) for r in lines(stream)] == [
        ("uvicorn.error", "info"),
        ("httpx", "info"),
    ]


def ask_and_log(provider: FakeProvider) -> list[dict]:
    stream = io.StringIO()
    configure(stream)
    settings = Settings(version="test-sha", data_dir=DATA, web_dist=None)
    client = TestClient(create_app(settings, provider), raise_server_exceptions=False)
    client.post("/ask", json={"question": "How do I make baklava?"})
    return [r for r in lines(stream) if r.get("event") == "ask"]


def test_the_request_trace_is_an_info_line_with_its_fields_at_the_top(
    restore_logging: None, tmp_path: Path
) -> None:
    (trace,) = ask_and_log(FakeProvider())
    assert trace["level"] == "info"
    assert trace["status"] == 200
    assert {"request_id", "version", "stages_ms", "usage"} <= trace.keys()
    log = tmp_path / "server.log"
    log.write_text(json.dumps(trace) + "\n")
    assert list(read_traces(log)) == [trace["request_id"]]


def test_a_5xx_trace_is_an_error_line(restore_logging: None) -> None:
    class Down(FakeProvider):
        async def extract(self, question: str):  # type: ignore[override]
            raise ProviderUnavailable("HTTP 400")

    (trace,) = ask_and_log(Down())
    assert trace["status"] == 503
    assert trace["level"] == "error"
