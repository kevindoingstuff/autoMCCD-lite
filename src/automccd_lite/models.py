"""Domain types that flow between the stages.

Record -> Draft -> RuleCheck -> Coding -> Route -> Result.

The MCCD itself stays a dict: it is the JSON the model returned under the
strict schema, and the validators and the ICD client read it as such.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# The interval fields DORIS takes are ISO-8601 durations. Note the T: P2M is
# two MONTHS, PT2M two MINUTES.
ISO_DURATION = {
    "minutes": "PT{}M",
    "hours": "PT{}H",
    "days": "P{}D",
    "weeks": "P{}W",
    "months": "P{}M",
    "years": "P{}Y",
}


def iso_duration(value: float | int | None, unit: str | None) -> str:
    """{value, unit} -> ISO-8601 duration, or the empty string when not expressible."""
    if value is None or unit not in ISO_DURATION:
        return ""
    n = int(value) if float(value).is_integer() else float(value)
    return ISO_DURATION[unit].format(n)


class Note(BaseModel):
    name: str = Field(min_length=1)
    text: str


class Record(BaseModel):
    """One death record as the API receives it. Sex and age are optional;
    when either is missing the certificate routes to review."""

    record_id: str = Field(min_length=1)
    sex: Literal["male", "female"] | None = None
    age: float | None = Field(default=None, ge=0)
    age_unit: Literal["minutes", "hours", "days", "weeks", "months", "years"] | None = None
    registry_info: str | None = None  # registry metadata; never evidence
    notes: list[Note] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_note_names(self) -> Record:
        names = [n.name for n in self.notes]
        if len(set(names)) != len(names):
            raise ValueError("note names must be unique")
        if (self.age is None) != (self.age_unit is None):
            raise ValueError("age and age_unit must be given together")
        return self

    @property
    def fields(self) -> dict[str, str]:
        """evidence_field name -> text."""
        return {n.name: n.text for n in self.notes}

    @property
    def age_iso(self) -> str | None:
        return iso_duration(self.age, self.age_unit) or None

    @property
    def demographics(self) -> dict[str, str | None]:
        return {"sex": self.sex, "age_iso": self.age_iso}

    @property
    def demographics_missing(self) -> bool:
        return self.sex is None or self.age_iso is None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            calls=self.calls + other.calls,
        )


DraftStatus = Literal["ok", "refused", "truncated", "unparsable"]


class Draft(BaseModel):
    """One model reply. `ok` carries a parsed JSON object; `unparsable` is a
    reply that is not one (strict mode should prevent it); `refused` and
    `truncated` carry no certificate and go straight to review."""

    status: DraftStatus
    certificate: dict[str, Any] | None = None
    reason: str | None = None
    model: str | None = None
    usage: Usage = Field(default_factory=Usage)
    seconds: float | None = None


class Route(StrEnum):
    """Two routes. A declined record (out of scope, insufficient information)
    is `review` with a `declined:*` reason, not a route of its own."""

    AUTO = "auto"
    REVIEW = "review"


class RuleCheck(BaseModel):
    """The gate's verdict on one draft."""

    schema_ok: bool
    rules_passed: bool
    broken_rules: list[str] = Field(default_factory=list)  # rule ids with an error
    messages: list[str] = Field(default_factory=list)  # every error and warning, readable
    assessment: str | None = None
    decline_reason: str | None = None
    confidence: str | None = None
    flags: list[str] = Field(default_factory=list)
    model_chain: list[str] = Field(default_factory=list)
    triage: Route
    # Revision outcome, read by routing and provenance.
    rules_fixed: bool = False
    before_fix: dict[str, Any] | None = None
    fix_rejected: str | None = None
    terms_fixed: Literal["applied", "unchanged", "rejected"] | None = None
    term_changes: list[list[str]] = Field(default_factory=list)
    term_reverted: list[list[Any]] = Field(default_factory=list)

    @property
    def needs_fix(self) -> bool:
        return not self.schema_ok or not self.rules_passed


class Coding(BaseModel):
    """What WHO's tools said about one certificate."""

    codes: dict[str, list[str | None]]
    uncoded_part1: list[int] = Field(default_factory=list)
    match_scores: list[float | None] = Field(default_factory=list)
    autocode: list[dict[str, Any]] = Field(default_factory=list)
    doris: dict[str, Any]
    codedit: dict[str, Any]
    codedit_cause: dict[str, Any] | None = None


class Decision(BaseModel):
    route: Route
    reasons: list[str] = Field(default_factory=list)


class CodingSummary(BaseModel):
    underlying_cause: str | None = None
    underlying_cause_full: str | None = None
    codes: dict[str, list[str | None]] = Field(default_factory=dict)
    match_scores: list[float | None] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    cause_warning: str | None = None


class Versions(BaseModel):
    prompt_version: str
    prompt_sha1: str
    schema_version: str
    model: str
    icd_release: str
    package_version: str


class Result(BaseModel):
    """What the API returns and what Blob stores."""

    record_id: str
    route: Route
    reasons: list[str]
    certificate: dict[str, Any] | None
    coding: CodingSummary | None
    versions: Versions
    usage: Usage
    timings_ms: dict[str, int]
    # Provenance: everything a reviewer or an audit needs, kept whole.
    draft_status: DraftStatus
    validation: RuleCheck | None
    coded: Coding | None
    icd_error: str | None = None
