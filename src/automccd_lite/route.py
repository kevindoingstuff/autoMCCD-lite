"""route_draft: the route and its reasons.

No coding means no auto. Every situation that keeps a certificate from
auto-acceptance leaves a stable reason code, `source:what`, so the response
and the stored row say why a human is looking at it. A declined record is
`review` with a `declined:*` reason.
"""

from __future__ import annotations

from automccd_lite.models import Coding, Decision, Draft, Record, Route, RuleCheck


def route_draft(
    record: Record,
    draft: Draft,
    validation: RuleCheck | None,
    coded: Coding | None,
    icd_error: str | None,
    min_score: float,
) -> Decision:
    if draft.status == "refused":
        return Decision(route=Route.REVIEW, reasons=["model:refused"])
    if draft.status == "truncated":
        return Decision(route=Route.REVIEW, reasons=["model:truncated"])
    if validation is None:
        return Decision(route=Route.REVIEW, reasons=["model:unparsable"])

    if validation.assessment == "out_of_scope":
        return Decision(route=Route.REVIEW, reasons=["declined:out_of_scope"])
    if validation.assessment == "insufficient_information":
        return Decision(route=Route.REVIEW, reasons=["declined:insufficient"])

    reasons: list[str] = []
    if not validation.schema_ok:
        reasons.append("schema:invalid")
    reasons += [f"rule:{rule}" for rule in validation.broken_rules]
    if validation.rules_fixed or validation.fix_rejected:
        reasons.append("fix:rules")
    if validation.terms_fixed == "applied":
        reasons.append("fix:terms")
    if validation.confidence != "high":
        reasons.append("model:low_confidence")

    if icd_error:
        reasons.append(icd_error)
    elif coded is None:
        reasons.append("who:not_coded")
    else:
        if coded.uncoded_part1:
            reasons.append("who:line_uncoded")
        if any(s is None or float(s) < min_score for s in coded.match_scores):
            reasons.append("who:low_match")
        if not coded.doris.get("ucod_stem_code"):
            reasons.append("who:no_cause")
        issues = list(coded.codedit.get("issue_ids") or [])
        if coded.codedit_cause:
            issues += [i for i in coded.codedit_cause.get("issue_ids") or [] if i not in issues]
        reasons += [f"quality:{i}" for i in issues]

    if record.demographics_missing:
        reasons.append("record:demographics_missing")

    return Decision(route=Route.AUTO if not reasons else Route.REVIEW, reasons=reasons)
