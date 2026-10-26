"""draft_certificate: ask the model for a draft certificate.

`complete` is the only function that talks to the model. The first draft and
both revisions use it with different user messages. It returns a Draft and
never raises on what the model said; it raises ModelUnavailable only when the
service could not be reached after the SDK's own retries.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from functools import partial
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from automccd_lite.config import DraftCfg, Settings, settings
from automccd_lite.models import Draft, Record, Usage
from automccd_lite.prompt import build_prompt, system_prompt

Ask = Callable[[str], Draft]
"""A call to the model with a user message, system prompt and schema fixed."""


class ModelUnavailable(RuntimeError):
    """429 or 5xx that outlasted the SDK's retries, or no connection. The
    request fails without a stored certificate; the caller may retry."""


def make_client(cfg: Settings | None = None) -> OpenAI:
    cfg = cfg or settings()
    return OpenAI(
        base_url=cfg.env.openai_base_url,
        api_key=cfg.env.openai_api_key,
        max_retries=cfg.draft.max_retries,
        timeout=cfg.draft.timeout_s,
    )


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
        return Draft(status="invalid", reason="empty reply", **base)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        return Draft(status="invalid", reason=f"not valid JSON: {e}"[:300], **base)
    if not isinstance(parsed, dict):
        return Draft(status="invalid", reason="reply is not a JSON object", **base)
    return Draft(status="ok", certificate=parsed, **base)


def asker(client: OpenAI) -> Ask:
    """The model call with everything but the user message fixed. The first
    draft and both revisions go through the same one, so the same strict
    schema applies to every reply."""
    sp = system_prompt()
    return partial(complete, client, sp.text, schema=sp.schema, cfg=settings().draft)


def draft_certificate(record: Record, ask: Ask) -> Draft:
    return ask(build_prompt(record).user)
