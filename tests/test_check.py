"""Ported from poc test_validate.py: every rule fires on a constructed
violation, and every rule stays silent where it should. The over-matching
cases are the ones that matter. The reference checks are in test_reference.py."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from automccd_lite.rules import apply_rules, validate_schema

NARRATIVE = (
    "The patient was admitted with fever and drowsiness. A diagnosis of "
    "Staphylococcus aureus meningitis was confirmed on CSF culture. He "
    "developed septic shock and died on the fourth day. He was a known "
    "diabetic on metformin for ten years."
)
FIELDS = {"case_narrative": NARRATIVE}


def cond(
    term,
    span="Staphylococcus aureus meningitis",
    basis="explicit_diagnosis",
    field="case_narrative",
    value=2,
    unit="days",
    certainty="documented",
):
    return {
        "certified_term": term,
        "evidence_field": field,
        "evidence_span": span,
        "basis": basis,
        "interval": {"value": value, "unit": unit, "certainty": certainty},
    }


def cert(
    part1,
    part2=None,
    assessment="certifiable",
    decline=None,
    flags=None,
    note="Chain supported by the documented culture result.",
):
    return {
        "record_id": "T01",
        "assessment": assessment,
        "decline_reason": decline,
        "part1": part1,
        "part2": part2 or [],
        "review": {"confidence": "high", "flags": flags or [], "note": note},
    }


VALID = cert(
    [
        cond("Septic shock", "septic shock", value=2, unit="days"),
        cond("Staphylococcus aureus meningitis", value=4, unit="days"),
    ]
)

OCR_FIELDS = {
    "case_narrative": "She was p1aced on blcod anticoagula-\nnts after the cor-\nonary event."
}
COMPOUND_FIELDS = {"case_narrative": "A 15-year history of non-\ninsulin-dependent diabetes."}
RANGE_FIELDS = {"case_narrative": "Troponin rose 1-2 hours after admission."}


@dataclass(frozen=True)
class Case:
    expect: str
    rule: str | None
    label: str
    cert: dict
    fields: dict

    def __str__(self) -> str:
        return f"{self.expect}:{self.rule or '*'}:{self.label}"


def fires(rule, label, c, fields=FIELDS):
    return Case("fires", rule, label, c, fields)


def silent(rule, label, c, fields=FIELDS):
    return Case("silent", rule, label, c, fields)


CASES = [
    fires("1", "span not in the record", cert([cond("Septic shock", span="fulminant hepatic necrosis on biopsy")])),
    fires("2", "mode of dying on 1a", cert([cond("Cardiac arrest", "septic shock")])),
    fires("2", "mode of dying on 1b", cert([cond("Septic shock", "septic shock"), cond("Cardiac arrest", "septic shock")])),
    fires("2", "mode of dying on the lowest line", cert([cond("Septic shock", "septic shock"), cond("Meningitis", "septic shock"), cond("Respiratory failure", "septic shock")])),
    fires("3", "two conditions on one line", cert([cond("Meningitis and septicaemia", "septic shock")])),
    fires("4", "abbreviation", cert([cond("Acute MI", "septic shock")])),
    fires("5", "duplicated term", cert([cond("Septic shock", "septic shock"), cond("Septic shock", "septic shock", value=3)])),
    fires("6", "interval decreases down the chain", cert([cond("Septic shock", "septic shock", value=5, unit="years"), cond("Staphylococcus aureus meningitis", value=2, unit="days")])),
    fires("7", "term in both Part I and Part II", cert([cond("Septic shock", "septic shock")], [cond("Septic shock", "septic shock")])),
    fires("8", "declined but carries a chain", cert([cond("Septic shock", "septic shock")], assessment="insufficient_information", decline="record_too_sparse")),
    fires("9", "ICD code in a term", cert([cond("Septic shock A41.9", "septic shock")])),
    fires("10", "phone number as a span", cert([cond("Septic shock", "septic shock")], [cond("Diabetes mellitus", "0771234567", basis="explicit_diagnosis")])),
    fires("2", "bare 'Shock' on 1a still fires", cert([cond("Shock", "septic shock")])),
    fires("2", "'Shock, unspecified' still fires", cert([cond("Shock, unspecified", "septic shock")])),
    fires("2", "'Shock unspecified' still fires", cert([cond("Shock unspecified", "septic shock")])),
    fires("2", "'Heart failure NOS' still fires", cert([cond("Heart failure NOS", "septic shock")])),
    fires("2", "'Heart failure due to hypertension' still fires", cert([cond("Heart failure due to hypertension", "septic shock")])),
    fires("1", "still fires when the OCR errors are 'corrected' in the span", cert([cond("Coronary thrombosis", "placed on blood anticoagulants")]), OCR_FIELDS),
    fires("1", "still fires when hyphens become spaces", cert([cond("Type 2 diabetes mellitus", "non insulin dependent diabetes")]), COMPOUND_FIELDS),
    fires("1", "a digit range is content: '12 hours' is not '1-2 hours'", cert([cond("Septic shock", "rose 12 hours")]), RANGE_FIELDS),
    fires("3", "'Ischaemic heart disease and <X>' is two conditions", cert([cond("Ischaemic heart disease and diabetes mellitus", "septic shock")])),
    fires("10", "a phone number and address in review.note", cert([cond("Septic shock", "septic shock")], note="Call daughter on 0771234567 at 12 Galle Road")),
    fires("10", "a phone number in decline_reason", cert([], assessment="insufficient_information", decline="Family reachable on +94 77 123 4567")),
    silent(None, "rule 2 congestive heart failure on 1a is correct (S17 reference)", cert([cond("Congestive heart failure", "septic shock", value=7, unit="years"), cond("Coronary heart disease", "septic shock", value=25, unit="years")])),
    silent(None, "rule 2 septic shock is a qualified shock (S01 reference)", cert([cond("Septic shock", "septic shock")])),
    silent(None, "rule 9 AIDS is a word, not an ICD-11 stem code (S18 reference)", cert([cond("Acquired immunodeficiency syndrome", "septic shock"), cond("HIV infection", "septic shock", value=17, unit="months")])),
    silent(None, "rule 3 'chronic obstructive pulmonary disease' survives the conjunction check", cert([cond("Chronic obstructive pulmonary disease", "septic shock")])),
    silent(None, "rule 6 unknown intervals are skipped, not flagged as decreasing (S22 reference)", cert([cond("Carbon monoxide poisoning", "septic shock", value=None, unit="unknown", certainty="unknown"), cond("Inhaled auto fumes", "septic shock", value=None, unit="unknown", certainty="unknown")], flags=["interval_undocumented"])),
    silent(None, "rule 2 'Dengue shock syndrome' is a named syndrome, not bare shock (H04)", cert([cond("Dengue shock syndrome", "septic shock")])),
    silent(None, "rule 2 'Staphylococcus aureus septic shock': organism before a qualified shock (FM-26)", cert([cond("Staphylococcus aureus septic shock", "septic shock")])),
    silent(None, "rule 2 'Shock lung' is ARDS, a named disease, not bare shock", cert([cond("Shock lung", "septic shock")])),
    silent(None, "rule 2 'Hypertensive heart failure' names its type", cert([cond("Hypertensive heart failure", "septic shock")])),
    silent(None, "rule 2 'Spinal shock' names its type", cert([cond("Spinal shock", "septic shock")])),
    silent(None, "rule 2 'Heart failure with reduced ejection fraction' names its type after the head", cert([cond("Heart failure with reduced ejection fraction", "septic shock")])),
    silent(None, "rule 9 an ISO duration ('PT2H') is not an ICD-11 code", cert([cond("Septic shock", "septic shock")], note="Onset PT2H before arrival")),
    silent(None, "rule 10 a date in a span is not a phone number", cert([cond("Septic shock", "septic shock")], note="Admitted 2003-04-01")),
    silent(None, "a well-formed certificate raises nothing", VALID),
    silent("1", "span across an end-of-line hyphen is verbatim", cert([cond("Coronary thrombosis", "p1aced on blcod anticoagulants")]), OCR_FIELDS),
    silent("1", "span ending at an end-of-line hyphen is verbatim (FM-16b)", cert([cond("Coronary thrombosis", "after the cor-")]), OCR_FIELDS),
    silent("1", "compound hyphen split across a line break is verbatim (FM-16c)", cert([cond("Type 2 diabetes mellitus", "non-insulin-dependent diabetes")]), COMPOUND_FIELDS),
    silent("1", "a digit range quoted as written is verbatim", cert([cond("Septic shock", "rose 1-2 hours")]), RANGE_FIELDS),
]  # fmt: skip


@pytest.mark.parametrize("case", CASES, ids=str)
def test_rule(case: Case):
    errs = apply_rules(case.cert, case.fields).errors
    hits = [e for e in errs if case.rule in (None, e.rule)]
    if case.expect == "fires":
        assert hits, f"{case.rule} did not fire; fired: {sorted({e.rule for e in errs})}"
    else:
        assert not hits, "over-match: " + "; ".join(str(e) for e in hits)


def test_valid_fixture_matches_schema():
    assert validate_schema(VALID) == []


def test_schema_rejects_extra_key():
    bad = dict(VALID, title="should not be here")
    assert any("title" in e for e in validate_schema(bad))
