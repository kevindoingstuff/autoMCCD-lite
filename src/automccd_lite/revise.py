"""revise_draft and revise_terms: the two bounded second chances.

revise_draft runs when the gate fails. The revision replaces the first draft
only if it is still an answer to the same question: it parses, and it keeps
the assessment unless the first draft broke the decline rule (V8) itself.
Adopted or rejected, the draft never goes auto.

revise_terms runs inside coding when WHO's coder cannot match a Part I term.
Accepted only if it is still the same certificate, structurally: only its
Part I certified_terms are taken over; Part II and every other Part I field
keep the original's values. Each restated term is then kept only if it codes
with a HIGHER autocode score than the original; otherwise that line reverts.
A revision may not make coding worse: "Rupture of ventricular wall" scored
0.11 and coded to a congenital malformation.

Calling cache.code() in revise_terms IS the first autocode pass; code_draft
reuses the cached entries afterwards.
"""

from __future__ import annotations

import copy
import json

from automccd_lite.code import CodeCache
from automccd_lite.draft import Ask
from automccd_lite.models import Draft, DraftValidation, Record
from automccd_lite.prompt import render
from automccd_lite.validate import never_auto, validate_draft

PREVIOUS_MAX_CHARS = 6000  # the budget the PoC's repair rates were measured with

REVISE_TEMPLATE = """{record}

---

Your previous answer for this record was:

{previous}

It failed these checks:

{failures}

Return the corrected JSON object in full. Change only what these failures require; keep
everything else as it was. If a span cannot be copied verbatim from the field you named,
shorten it to a few words that appear exactly in that field, or change the field."""


def needs_revision(draft: Draft, validation: DraftValidation | None) -> bool:
    if draft.status == "invalid":
        return True
    return validation is not None and validation.needs_revision


def previous_text(draft: Draft) -> str:
    if draft.certificate is not None:
        return json.dumps(draft.certificate, ensure_ascii=False)[:PREVIOUS_MAX_CHARS]
    return ""


def revise_message(record: Record, draft: Draft, validation: DraftValidation | None) -> str:
    if validation is None:
        fails = [f"The reply is not valid JSON ({draft.reason})."]
    else:
        fails = [f"schema: {e}" for e in validation.schema_errors] + validation.errors
    return REVISE_TEMPLATE.format(
        record=render(record),
        previous=previous_text(draft),
        failures="\n".join(f"- {f}" for f in fails),
    )


def revise_draft(
    record: Record, draft: Draft, validation: DraftValidation | None, ask: Ask
) -> tuple[Draft, DraftValidation | None]:
    """One revision call. Returns the (draft, validation) the pipeline goes
    on with: the revision when adopted, the first draft when rejected. Either
    way the returned draft's usage includes the revision call."""
    again = ask(revise_message(record, draft, validation))
    new_validation = (
        validate_draft(again.certificate, record.fields) if again.certificate is not None else None
    )
    keeps_question = (
        validation is None
        or (new_validation is not None and new_validation.assessment == validation.assessment)
        or "V8" in validation.rules_fired
    )
    if new_validation is not None and keeps_question:
        new_validation.revised = True
        new_validation.revise_before = (
            {
                "schema_ok": validation.schema_ok,
                "validator_passed": validation.validator_passed,
                "rules_fired": validation.rules_fired,
            }
            if validation is not None
            else {"status": draft.status, "reason": draft.reason}
        )
        never_auto(new_validation)
        again.usage = draft.usage + again.usage
        return again, new_validation
    why = again.reason if again.certificate is None else "changed the assessment"
    if validation is not None:
        validation.revise_rejected = f"rejected: {why}"
        never_auto(validation)
    draft.usage = draft.usage + again.usage
    return draft, validation


TERM_REVISE_TEMPLATE = """{record}

---

Your certificate for this record was:

{previous}

The WHO ICD-11 coding tool could not confidently match these certified terms:

{terms}

Restate each listed term as the standard ICD-11 name of the SAME condition (for example
"Coronary atherosclerosis" rather than "Coronary artery stenosis"). Do not change which
condition it is, its line, its evidence span, its basis or its interval, and do not touch
any other line. Keep every detail the record supports -- site, organism, histology -- and
never replace a named primary site with "unspecified". If the term is already the standard
name, keep it. Return the full JSON object."""


def low_score(score: float | None, min_score: float) -> bool:
    """An autocode match too weak to trust; a term with no score is one."""
    return score is None or float(score) < min_score


def low_score_terms(cert: dict, cache: CodeCache, min_score: float) -> list[tuple[str, dict]]:
    out = []
    for c in cert.get("part1") or []:
        t = c.get("certified_term") or ""
        if t:
            e = cache.code(t)
            if low_score(e.get("match_score"), min_score):
                out.append((t, e))
    return out


def term_revise_message(record: Record, cert: dict, low: list[tuple[str, dict]]) -> str:
    terms = "\n".join(
        f"- '{t}' (closest ICD-11 match: '{e.get('matched_text')}', score {e.get('match_score')})"
        for t, e in low
    )
    return TERM_REVISE_TEMPLATE.format(
        record=render(record), terms=terms, previous=json.dumps(cert, ensure_ascii=False)
    )


def scoped_to_terms(old: dict, new: dict) -> dict | None:
    """The old certificate with only the revision's Part I certified_terms
    taken over: Part II and every other Part I field stay as they were.
    None when the revision changed the number of Part I lines."""
    old_p1, new_p1 = old.get("part1") or [], new.get("part1") or []
    if len(old_p1) != len(new_p1):
        return None
    scoped = copy.deepcopy(old)
    for line, line_new in zip(scoped.get("part1") or [], new_p1, strict=True):
        if "certified_term" in line_new:
            line["certified_term"] = line_new["certified_term"]
    return scoped


def revise_terms(
    record: Record,
    draft: Draft,
    validation: DraftValidation,
    cache: CodeCache,
    ask: Ask,
    min_score: float,
) -> tuple[Draft, DraftValidation]:
    """The (draft, validation) the pipeline goes on with. Unchanged when no
    Part I term scores low."""
    cert = draft.certificate or {}
    low = low_score_terms(cert, cache, min_score)
    if not low:
        return draft, validation

    again = ask(term_revise_message(record, cert, low))
    total = draft.usage + again.usage
    new = again.certificate
    new_v = validate_draft(new, record.fields) if new is not None else None
    if (
        new is None
        or new_v is None
        or not new_v.validator_passed
        or new_v.assessment != validation.assessment
        or len(new_v.model_chain) != len(validation.model_chain)
    ):
        validation.term_revision = "rejected"
        draft.usage = total
        return draft, validation

    new = scoped_to_terms(cert, new)
    if new is None:
        validation.term_revision = "rejected"
        draft.usage = total
        return draft, validation
    again.certificate = new
    new_v = validate_draft(new, record.fields)

    # Per-term guard: a restated term must code better than the original.
    reverted: list[list] = []
    for line_new, line_old in zip(new.get("part1") or [], cert.get("part1") or [], strict=False):
        t_new, t_old = line_new.get("certified_term", ""), line_old.get("certified_term", "")
        if t_new != t_old:
            s_new = cache.code(t_new).get("match_score") or 0
            s_old = cache.code(t_old).get("match_score") or 0
            if float(s_new) <= float(s_old):
                line_new["certified_term"] = t_old
                reverted.append([t_old, t_new, round(float(s_old), 2), round(float(s_new), 2)])
    if reverted:
        new_v = validate_draft(new, record.fields)
        new_v.term_reverted = reverted
    changes = [
        [x, y] for x, y in zip(validation.model_chain, new_v.model_chain, strict=False) if x != y
    ]
    if not changes:
        validation.term_reverted = reverted
        validation.term_revision = "unchanged"
        draft.usage = total
        return draft, validation

    new_v.revised, new_v.revise_before = validation.revised, validation.revise_before
    new_v.revise_rejected = validation.revise_rejected
    new_v.term_revision = "applied"
    new_v.term_changes = changes
    never_auto(new_v)
    again.usage = total
    return again, new_v
