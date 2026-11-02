"""Step 3. Decide: the route, its reasons, and the Result that carries them.

The router is a table, not a judgement. Every situation that keeps a
certificate from auto-acceptance leaves a stable reason code, `source:what`,
so the response and the stored row say why a human is looking at it. No
reasons means `auto`; any reason means `review`. A declined record is
`review` with a `declined:*` reason.

decide writes nothing. The api saves the Result with store.save.
"""

from __future__ import annotations

from typing import Any

from automccd_lite import __version__
from automccd_lite.config import settings
from automccd_lite.draft import system_prompt
from automccd_lite.models import (
    Coding,
    CodingSummary,
    Draft,
    Record,
    Result,
    Route,
    RuleCheck,
    Versions,
)


def reasons_for(
    record: Record,
    draft: Draft,
    check: RuleCheck | None,
    coding: Coding | None,
    icd_error: str | None,
    min_score: float,
) -> list[str]:
    """Every reason, in a fixed order: model, declined, schema, rules, fixes,
    confidence, the WHO tools, the record."""
    if draft.status == "refused":
        return ["model:refused"]
    if draft.status == "truncated":
        return ["model:truncated"]
    if check is None:
        return ["model:unparsable"]

    if check.assessment == "out_of_scope":
        return ["declined:out_of_scope"]
    if check.assessment == "insufficient_information":
        return ["declined:insufficient"]

    reasons: list[str] = []
    if not check.schema_ok:
        reasons.append("schema:invalid")
    reasons += [f"rule:{rule}" for rule in check.broken_rules]
    if check.rules_fixed or check.fix_rejected:
        reasons.append("fix:rules")
    if check.terms_fixed == "applied":
        reasons.append("fix:terms")
    if check.confidence != "high":
        reasons.append("model:low_confidence")

    if icd_error:
        reasons.append(icd_error)
    elif coding is None:
        reasons.append("who:not_coded")
    else:
        if coding.uncoded_part1:
            reasons.append("who:line_uncoded")
        if any(s is None or float(s) < min_score for s in coding.match_scores):
            reasons.append("who:low_match")
        if not coding.doris.get("ucod_stem_code"):
            reasons.append("who:no_cause")
        issues = list(coding.codedit.get("issue_ids") or [])
        if coding.codedit_cause:
            issues += [i for i in coding.codedit_cause.get("issue_ids") or [] if i not in issues]
        reasons += [f"quality:{i}" for i in issues]

    if record.demographics_missing:
        reasons.append("record:demographics_missing")
    return reasons


def coding_summary(coding: Coding | None) -> CodingSummary | None:
    if coding is None:
        return None
    issues = list(coding.codedit.get("issue_ids") or [])
    if coding.codedit_cause:
        issues += [i for i in coding.codedit_cause.get("issue_ids") or [] if i not in issues]
    return CodingSummary(
        underlying_cause=coding.doris.get("ucod_stem_code"),
        underlying_cause_full=coding.doris.get("ucod_code"),
        codes=coding.codes,
        match_scores=coding.match_scores,
        quality_issues=issues,
        cause_warning=coding.doris.get("warning"),
    )


def versions(model: str | None) -> Versions:
    sp = system_prompt()
    cfg = settings()
    return Versions(
        prompt_version=sp.version,
        prompt_sha1=sp.sha1,
        schema_version=sp.schema_version,
        model=model or cfg.draft.model,
        icd_release=cfg.check.icd_release,
        package_version=__version__,
    )


def decide(
    record: Record,
    draft: Draft,
    check: RuleCheck | None,
    coding: Coding | None,
    icd_error: str | None = None,
    timings_ms: dict[str, int] | None = None,
) -> Result:
    """The route and its reasons, and the Result that carries everything the
    steps produced."""
    reasons = reasons_for(record, draft, check, coding, icd_error, settings().check.min_match_score)
    return Result(
        record_id=record.record_id,
        route=Route.REVIEW if reasons else Route.AUTO,
        reasons=reasons,
        certificate=draft.certificate,
        coding=coding_summary(coding),
        versions=versions(draft.model),
        usage=draft.usage,
        timings_ms=dict(timings_ms or {}),
        draft_status=draft.status,
        rule_check=check,
        who_replies=coding,
        icd_error=icd_error,
    )


def as_response(result: Result) -> dict[str, Any]:
    """The api body: the Result without the bulky provenance, which stays in
    the stored row."""
    return result.model_dump(
        mode="json", exclude={"rule_check", "who_replies", "icd_error", "draft_status"}
    )
