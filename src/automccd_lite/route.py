"""route_draft: the route and its reasons.

No coding means no auto. Every situation that keeps a certificate from
auto-acceptance leaves a stable reason code, so the response and the
provenance row say why a human is looking at it. A declined record is
`review` with an `assessment:*` reason.
"""

from __future__ import annotations

from automccd_lite.models import CodedDraft, Decision, Draft, DraftValidation, Record, Route


def route_draft(
    record: Record,
    draft: Draft,
    validation: DraftValidation | None,
    coded: CodedDraft | None,
    icd_error: str | None,
    min_score: float,
) -> Decision:
    if draft.status == "refused":
        return Decision(route=Route.REVIEW, reasons=["model_refused"])
    if draft.status == "truncated":
        return Decision(route=Route.REVIEW, reasons=["model_truncated"])
    if validation is None:
        return Decision(route=Route.REVIEW, reasons=["model_invalid"])

    if validation.assessment == "out_of_scope":
        return Decision(route=Route.REVIEW, reasons=["assessment:out_of_scope"])
    if validation.assessment == "insufficient_information":
        return Decision(route=Route.REVIEW, reasons=["assessment:insufficient"])

    reasons: list[str] = []
    if not validation.schema_ok:
        reasons.append("schema_invalid")
    reasons += [f"validator:{rule}" for rule in validation.rules_fired]
    if validation.revised or validation.revise_rejected:
        reasons.append("revised")
    if validation.term_revision == "applied":
        reasons.append("terms_revised")
    if validation.confidence != "high":
        reasons.append("low_confidence")

    if icd_error:
        reasons.append(icd_error)
    elif coded is None:
        reasons.append("not_coded")
    else:
        if coded.uncoded_part1:
            reasons.append("part1_line_uncoded")
        if any(s is None or float(s) < min_score for s in coded.code_scores):
            reasons.append("autocode_low_score")
        if not coded.doris.get("ucod_stem_code"):
            reasons.append("no_ucod")
        issues = list(coded.codedit.get("issue_ids") or [])
        if coded.codedit_ucod:
            issues += [i for i in coded.codedit_ucod.get("issue_ids") or [] if i not in issues]
        reasons += [f"codedit:{i}" for i in issues]

    if record.demographics_missing:
        reasons.append("demographics_missing")

    return Decision(route=Route.AUTO if not reasons else Route.REVIEW, reasons=reasons)
