"""Step 1. Draft: the record becomes a prompt, and one model call returns a draft.

The system prompt is the prompt file, the output schema and the reply rule,
assembled once at import. Its sha1 goes into every result's versions block.
Neither the assembly nor the rule may change by a byte: evaluated runs are
identified by this hash.

`complete` is the only function that talks to the model. The first draft and
both fixes in the Check step use it with different user messages. It returns
a Draft and never raises on what the model said; it raises ModelUnavailable
only when the service could not be reached after the SDK's own retries.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from automccd_lite.config import PROMPTS_DIR, DraftCfg, Settings, settings
from automccd_lite.models import Draft, Record, Usage

__all__ = ["Ask", "ModelUnavailable", "connect_model", "draft_certificate"]

# ---------------------------------------------------------------------------
# the prompt

REPLY_RULE = """\
Return one JSON object and nothing else — no commentary, no markdown fences,
no explanation before or after. Set `record_id` to the record_id exactly as given."""


def assemble(prompt: str, schema_text: str, reply_rule: str = REPLY_RULE) -> str:
    """The prompt, the schema, the reply rule."""
    return (
        prompt
        + "\n\n---\n\n"
        + "## Output schema\n\n```json\n"
        + schema_text
        + "\n```\n\n"
        + reply_rule
        + "\n"
    )


@dataclass(frozen=True)
class SystemPrompt:
    text: str
    sha1: str
    version: str
    schema: dict[str, Any]
    schema_version: str


@lru_cache(maxsize=1)
def system_prompt() -> SystemPrompt:
    cfg = settings().draft
    prompt_text = (PROMPTS_DIR / "mccd" / f"v{cfg.prompt_version}.md").read_text(encoding="utf-8")
    schema_text = (PROMPTS_DIR / "schemas" / f"{cfg.schema_name}.schema.json").read_text(
        encoding="utf-8"
    )
    text = assemble(prompt_text, schema_text)
    schema = json.loads(schema_text)
    return SystemPrompt(
        text=text,
        sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),  # noqa: S324 - identity, not security
        version=cfg.prompt_version,
        schema=schema,
        schema_version=str(schema.get("$id", "")).rsplit("/", 1)[-1] or "unknown",
    )


def render(record: Record) -> str:
    """The exact text the model sees."""
    sex = record.sex or "not recorded"
    age = record.age_iso or "not recorded"
    lines = [f"record_id: {record.record_id}", f"decedent: sex={sex}; age={age}"]
    if record.registry_info:
        # The label stays "context": the model was evaluated on this exact text.
        label = "context (registry metadata, not a record field -- do not cite)"
        lines.append(f"{label}: {record.registry_info}")
    lines.append("")
    for name, text in record.fields.items():
        lines.append(f"=== field: {name} ===")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    sha1: str


def build_prompt(record: Record) -> Prompt:
    sp = system_prompt()
    return Prompt(system=sp.text, user=render(record), sha1=sp.sha1)


# ---------------------------------------------------------------------------
# the model call

Ask = Callable[[str], Draft]
"""A call to the model with a user message, system prompt and schema fixed."""


class ModelUnavailable(RuntimeError):
    """429 or 5xx that outlasted the SDK's retries, or no connection. The
    request fails without a stored certificate; the caller may retry."""


def response_format(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": "mccd", "strict": True, "schema": schema},
    }


def complete(
    client: OpenAI, system: str, user: str, schema: dict[str, Any], cfg: DraftCfg
) -> Draft:
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": response_format(schema),
        "max_completion_tokens": cfg.max_completion_tokens,
    }
    # Reasoning models reject temperature and need reasoning_effort; others
    # the reverse. None means "do not send".
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    if cfg.seed is not None:
        kwargs["seed"] = cfg.seed
    if cfg.reasoning_effort is not None:
        kwargs["reasoning_effort"] = cfg.reasoning_effort

    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(**kwargs)
    except (APIConnectionError, APITimeoutError, RateLimitError) as e:
        raise ModelUnavailable(f"{type(e).__name__}") from e
    except APIStatusError as e:
        if e.status_code >= 500:
            raise ModelUnavailable(f"HTTP {e.status_code}") from e
        raise  # 4xx is our request being wrong, not the service being down
    seconds = round(time.perf_counter() - t0, 2)

    u = r.usage
    det = getattr(u, "completion_tokens_details", None) if u else None
    usage = Usage(
        prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        reasoning_tokens=(getattr(det, "reasoning_tokens", 0) or 0) if det else 0,
        calls=1,
    )
    base = {"model": r.model or cfg.model, "usage": usage, "seconds": seconds}

    choice = r.choices[0]
    msg = choice.message
    refusal = getattr(msg, "refusal", None)
    if refusal or choice.finish_reason == "content_filter":
        return Draft(status="refused", reason=(refusal or "content_filter")[:300], **base)
    if choice.finish_reason == "length":
        # On a reasoning model this arrives with empty content.
        return Draft(status="truncated", reason="finish_reason=length", **base)
    content = msg.content or ""
    if not content.strip():
        return Draft(status="unparsable", reason="empty reply", **base)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        return Draft(status="unparsable", reason=f"not valid JSON: {e}"[:300], **base)
    if not isinstance(parsed, dict):
        return Draft(status="unparsable", reason="reply is not a JSON object", **base)
    return Draft(status="ok", certificate=parsed, **base)


def connect_model(cfg: Settings | None = None) -> Ask:
    """The model call with everything but the user message fixed. The first
    draft and both fixes go through the same one, so the same strict schema
    applies to every reply."""
    cfg = cfg or settings()
    client = OpenAI(
        base_url=cfg.env.openai_base_url,
        api_key=cfg.env.openai_api_key,
        max_retries=cfg.draft.max_retries,
        timeout=cfg.draft.timeout_s,
    )
    sp = system_prompt()
    return partial(complete, client, sp.text, schema=sp.schema, cfg=cfg.draft)


def draft_certificate(record: Record, ask: Ask) -> Draft:
    return ask(build_prompt(record).user)
