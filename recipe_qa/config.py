"""Runtime settings, read once from the environment (SPEC §2.3, §8)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent
BUILD_SHA_FILE = Path(__file__).resolve().parent / "BUILD_SHA"
"""Written by CI into the build context and copied into the image (ADR 003).
Not committed; a checkout without it reports version "dev"."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = "dev"
    """Git SHA of the build; reported by /healthz so CI can confirm a deploy."""
    data_dir: Path = ROOT / "data"
    web_dist: Path | None = ROOT / "web" / "dist"
    provider: Literal["anthropic", "fake"] = "anthropic"
    model: str = "claude-haiku-5-5"
    provider_timeout_s: float = 8.0
    request_deadline_s: float = 12.0
    rate_limit_per_minute: int = 30
    max_concurrent_requests: int = 4
    max_body_bytes: int = 8 * 1024

    @classmethod
    def from_env(cls, build_sha_file: Path = BUILD_SHA_FILE) -> Settings:
        env = os.environ
        values: dict[str, object] = {"version": _build_sha(build_sha_file) or "dev"}
        for field, var in [
            ("provider", "RECIPE_QA_PROVIDER"),
            ("model", "RECIPE_QA_MODEL"),
            ("data_dir", "RECIPE_QA_DATA_DIR"),
            ("web_dist", "RECIPE_QA_WEB_DIST"),
        ]:
            if env.get(var):
                values[field] = env[var]
        return cls.model_validate(values)


def _build_sha(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""
