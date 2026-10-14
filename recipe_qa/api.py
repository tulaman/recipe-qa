"""HTTP layer (SPEC §2): request validation, limits, status mapping, logs.

Run with `uvicorn recipe_qa.api:app`. Business outcomes are always 200;
technical failures use the error schema and never look like a refusal.
Every response carries X-Request-ID, and every /ask request emits one
structured JSON log line with what is needed to explain its answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from recipe_qa.config import Settings
from recipe_qa.constraints import AliasTable
from recipe_qa.corpus import load_manifest, load_recipes
from recipe_qa.generation import PROMPT_VERSION
from recipe_qa.providers import make_provider
from recipe_qa.providers.base import Provider
from recipe_qa.schemas import AskRequest, AskResponse, ErrorDetail, ErrorResponse
from recipe_qa.service import AskService, UpstreamInvalid, UpstreamUnavailable

log = logging.getLogger("recipe_qa.request")


class InvalidRequest(Exception):
    pass


class RateLimiter:
    """Sliding one-minute window per client key, in process memory.

    Clients with no request inside the window are dropped at most once a
    window, so memory is bounded by the clients active in the last two
    minutes, not by every address ever seen.
    """

    WINDOW_S = 60.0

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._last_sweep: float | None = None

    def tracked_clients(self) -> int:
        return len(self._hits)

    def _sweep(self, now: float) -> None:
        if self._last_sweep is None:
            self._last_sweep = now
        if now - self._last_sweep < self.WINDOW_S:
            return
        cutoff = now - self.WINDOW_S
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
        self._last_sweep = now

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._sweep(now)
        hits = self._hits[key]
        while hits and hits[0] <= now - self.WINDOW_S:
            hits.popleft()
        if len(hits) >= self._per_minute:
            return False
        hits.append(now)
        return True


def client_key(request: Request) -> str:
    # Railway's edge proxy sets X-Real-IP; locally there is no proxy.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "-")


async def read_question(request: Request, max_bytes: int) -> str:
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != "application/json":
        raise InvalidRequest("Content-Type must be application/json")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise InvalidRequest(f"request body exceeds {max_bytes} bytes")
    try:
        return AskRequest.model_validate_json(bytes(body)).question
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        raise InvalidRequest(f"{where}: {first['msg']}") from exc


def error(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=request_id))  # type: ignore[arg-type]
    return JSONResponse(body.model_dump(), status_code=status)


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    manifest = load_manifest(settings.data_dir / "manifest.json")
    service = AskService(
        load_recipes(settings.data_dir / "recipes.jsonl"),
        manifest,
        provider or make_provider(settings),
        settings.request_deadline_s,
        AliasTable.load(settings.data_dir / "aliases.json"),
    )
    limiter = RateLimiter(settings.rate_limit_per_minute)
    in_flight = asyncio.Semaphore(settings.max_concurrent_requests)

    app = FastAPI(title="Recipe Q&A", version=settings.version, redoc_url=None)

    @app.middleware("http")
    async def request_id(request: Request, call_next: Any) -> Response:
        request.state.request_id = str(uuid.uuid4())
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.post("/ask", response_model=AskResponse, responses={"4XX": {"model": ErrorResponse}})
    async def ask(request: Request) -> Response:
        rid: str = request.state.request_id
        trace: dict[str, Any] = {
            "event": "ask",
            "request_id": rid,
            "version": settings.version,
            "model": service.provider.model,
            "prompt_version": PROMPT_VERSION,
            "corpus_version": service.corpus_version,
        }
        started = time.perf_counter()
        response = await _ask(request, rid, trace)
        trace.update(status=response.status_code, latency_ms=_ms_since(started))
        log.info(json.dumps(trace, ensure_ascii=False, default=str))
        return response

    async def _ask(request: Request, rid: str, trace: dict[str, Any]) -> Response:
        if not limiter.allow(client_key(request)):
            trace["error"] = "rate_limited"
            return error(429, "rate_limited", "Too many requests; try again in a minute.", rid)
        try:
            question = await read_question(request, settings.max_body_bytes)
        except InvalidRequest as exc:
            trace["error"] = "invalid_request"
            return error(422, "invalid_request", str(exc), rid)
        trace["question"] = question
        if in_flight.locked():
            trace["error"] = "rate_limited"
            return error(429, "rate_limited", "The service is busy; try again shortly.", rid)
        async with in_flight:
            try:
                answer = await service.ask(question, rid, trace)
            except UpstreamUnavailable as exc:
                trace["error"] = f"upstream_unavailable: {exc}"
                return error(503, "upstream_unavailable", "The language model is unavailable.", rid)
            except UpstreamInvalid as exc:
                trace["error"] = f"upstream_invalid: {exc}"
                return error(502, "upstream_invalid", "The model gave no usable answer.", rid)
            except Exception as exc:
                trace["error"] = f"internal_error: {type(exc).__name__}: {exc}"
                log.exception("unhandled error in /ask request_id=%s", rid)
                return error(500, "internal_error", "Unexpected server error.", rid)
        return JSONResponse(answer.model_dump(mode="json"))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "version": settings.version,
            "corpus_version": service.corpus_version,
        }

    @app.get("/schema")
    async def schema() -> dict[str, Any]:
        return AskResponse.model_json_schema()

    if settings.web_dist and (settings.web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="ui")

    return app


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def __getattr__(name: str) -> FastAPI:
    # `uvicorn recipe_qa.api:app` builds the app on first access, so importing
    # this module (tests, tools) never reads the environment or the corpus.
    if name == "app":
        globals()["app"] = application = create_app()
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        return application
    raise AttributeError(name)
