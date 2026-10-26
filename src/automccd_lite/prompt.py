"""build_prompt: the text the model is given.

The system prompt is the prompt file, the output schema and the reply rule,
assembled once at import. Its sha1 goes into every certificate's versions
block. Neither the assembly nor the rule may change by a byte: evaluated
runs are identified by this hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from automccd_lite.config import PROMPTS_DIR, settings
from automccd_lite.models import Record

ONE_CASE_RULE = """\
Return one JSON object and nothing else — no commentary, no markdown fences,
no explanation before or after. Set `record_id` to the record_id exactly as given."""


def assemble(prompt: str, schema_text: str, reply_rule: str = ONE_CASE_RULE) -> str:
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
    cfg = settings().prompt
    prompt_text = (PROMPTS_DIR / "mccd" / f"v{cfg.version}.md").read_text(encoding="utf-8")
    schema_text = (PROMPTS_DIR / "schemas" / f"{cfg.schema_name}.schema.json").read_text(
        encoding="utf-8"
    )
    text = assemble(prompt_text, schema_text)
    schema = json.loads(schema_text)
    return SystemPrompt(
        text=text,
        sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),  # noqa: S324 - identity, not security
        version=cfg.version,
        schema=schema,
        schema_version=str(schema.get("$id", "")).rsplit("/", 1)[-1] or "unknown",
    )


def render(record: Record) -> str:
    """The exact text the model sees."""
    sex = record.sex or "not recorded"
    age = record.age_iso or "not recorded"
    lines = [f"record_id: {record.record_id}", f"decedent: sex={sex}; age={age}"]
    if record.context:
        lines.append(
            f"context (registry metadata, not a record field -- do not cite): {record.context}"
        )
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
