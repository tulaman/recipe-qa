"""Runtime settings, read once from the environment (SPEC §2.3, §8)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent


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
    def from_env(cls) -> Settings:
        env = os.environ
        values: dict[str, object] = {"version": env.get("GIT_SHA") or "dev"}
        for field, var in [
            ("provider", "RECIPE_QA_PROVIDER"),
            ("model", "RECIPE_QA_MODEL"),
            ("data_dir", "RECIPE_QA_DATA_DIR"),
            ("web_dist", "RECIPE_QA_WEB_DIST"),
        ]:
            if env.get(var):
                values[field] = env[var]
        return cls.model_validate(values)
