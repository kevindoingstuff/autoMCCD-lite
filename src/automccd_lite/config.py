"""Runtime settings: config/default.yaml merged with the environment.

Tuning lives in YAML, one section per step that has settings (Draft and
Check; Decide has none). Endpoints and secrets come only from
environment variables, using the names the vendor SDKs already read, so
nothing secret is ever written to a file this package owns.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "default.yaml"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"


class DraftCfg(BaseModel):
    prompt_version: str
    schema_name: str = Field(alias="schema")
    model: str
    temperature: float | None = None  # None: do not send (reasoning models reject it)
    seed: int | None = None
    reasoning_effort: str | None = None  # None: do not send (non-reasoning models)
    max_completion_tokens: int = 8000
    timeout_s: float = 120
    max_retries: int = 4

    model_config = {"populate_by_name": True}


class CheckCfg(BaseModel):
    fix_rules_attempts: int = 1
    fix_terms_attempts: int = 1
    min_match_score: float = 0.5
    icd_release: str
    linearization: str = "mms"
    timeout_s: float = 30
    max_retries: int = 2


class Env(BaseModel):
    """Only what is read from the environment. Values are never logged."""

    openai_base_url: str | None
    openai_api_key: str | None
    icd_api_url: str
    store_dir: Path  # the SQLite file and the certificate JSON files live here
    api_port: int


class Settings(BaseModel):
    draft: DraftCfg
    check: CheckCfg
    env: Env


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def settings(path: Path = CONFIG_PATH) -> Settings:
    load_dotenv(ROOT / ".env")  # the api, the eval and the notebooks all read the same file
    raw = _load_yaml(path)
    if model := os.environ.get("LLM_MODEL"):
        raw.setdefault("draft", {})["model"] = model
    raw["env"] = {
        "openai_base_url": os.environ.get("OPENAI_BASE_URL"),
        "openai_api_key": os.environ.get("OPENAI_API_KEY"),
        "icd_api_url": os.environ.get("ICD_API_URL", "http://localhost:8082"),
        "store_dir": Path(os.environ.get("STORE_DIR") or ROOT / "store"),
        "api_port": int(os.environ.get("API_PORT", "8000")),
    }
    return Settings.model_validate(raw)
