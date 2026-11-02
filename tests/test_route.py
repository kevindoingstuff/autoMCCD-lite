"""One test per row of the routing table: two routes, one reason code per cause."""

import pytest

from automccd_lite.models import Coding, Draft, Route
from automccd_lite.route import route_draft
from automccd_lite.validate import validate_draft
from tests.conftest import cond, good_cert, make_record, ok

MIN = 0.5


def coded(scores=(0.9, 0.9), ucod="1D01.0", issues=(), ucod_issues=(), uncoded=()):
    return Coding(
        codes={"part1": ["1G41", "1D01.0"], "part2": ["5A11"]},
        uncoded_part1=list(uncoded),
        match_scores=list(scores),
        doris={"ucod_stem_code": ucod, "ucod_code": ucod, "warning": None},
        codedit={"issue_ids": list(issues)},
        codedit_cause={"issue_ids": list(ucod_issues)} if ucod else None,
    )


CLEAN = object()  # "use a clean Coding"; None means "not coded"


def route(cert=None, draft=None, c=CLEAN, icd_error=None, rec=None):
    rec = rec or make_record()
    draft = draft or ok(cert or good_cert())
    v = validate_draft(draft.certificate, rec.fields) if draft.certificate else None
    return route_draft(rec, draft, v, coded() if c is CLEAN else c, icd_error, MIN)


def test_clean_coded_certificate_is_auto():
    d = route()
    assert d.route == Route.AUTO and d.reasons == []


@pytest.mark.parametrize(
    ("draft", "reason"),
    [
        (Draft(status="refused", reason="filter"), "model_refused"),
        (Draft(status="truncated", reason="length"), "model_truncated"),
        (Draft(status="unparsable", reason="junk"), "model_invalid"),
    ],
)
def test_model_failures_go_to_review(draft, reason):
    d = route(draft=draft, c=None)
    assert d.route == Route.REVIEW and d.reasons == [reason]


def test_declined_records_are_review_with_an_assessment_reason():
    oos = {**good_cert(), "assessment": "out_of_scope", "decline_reason": "external_cause",
           "part1": [], "part2": []}  # fmt: skip
    d = route(oos, c=None)
    assert d.route == Route.REVIEW and d.reasons == ["assessment:out_of_scope"]
    ins = {**oos, "assessment": "insufficient_information", "decline_reason": "record_too_sparse"}
    d = route(ins, c=None)
    assert d.route == Route.REVIEW and d.reasons == ["assessment:insufficient"]


def test_declined_triage_is_review():
    oos = {**good_cert(), "assessment": "out_of_scope", "decline_reason": "external_cause",
           "part1": [], "part2": []}  # fmt: skip
    assert validate_draft(oos, make_record().fields).triage == Route.REVIEW


def test_validator_errors_name_the_rule():
    c = good_cert()
    c["part1"][0] = cond("Cardiac arrest", "septic shock", 1)
    d = route(c)
    assert d.route == Route.REVIEW and "validator:V2" in d.reasons


def test_low_confidence_is_review():
    c = good_cert()
    c["review"]["confidence"] = "moderate"
    assert route(c).reasons == ["low_confidence"]


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"uncoded": [1]}, "part1_line_uncoded"),
        ({"scores": (0.9, 0.3)}, "autocode_low_score"),
        ({"scores": (0.9, None)}, "autocode_low_score"),
        ({"ucod": None}, "no_ucod"),
        ({"issues": ["FER-CE-993"]}, "codedit:FER-CE-993"),
        ({"ucod_issues": ["FER-CE-931"]}, "codedit:FER-CE-931"),
    ],
)
def test_coding_findings_are_review(kw, reason):
    d = route(c=coded(**kw))
    assert d.route == Route.REVIEW and reason in d.reasons


def test_icd_unavailable_means_no_auto():
    d = route(c=None, icd_error="icd_unavailable")
    assert d.route == Route.REVIEW and d.reasons == ["icd_unavailable"]


def test_missing_demographics_is_review_even_when_clean():
    rec = make_record(sex=None, age=None, age_unit=None)
    d = route(rec=rec)
    assert d.route == Route.REVIEW and d.reasons == ["demographics_missing"]


def test_revised_never_auto():
    rec = make_record()
    draft = ok(good_cert())
    v = validate_draft(draft.certificate, rec.fields)
    v.rules_fixed = True
    d = route_draft(rec, draft, v, coded(), None, MIN)
    assert d.route == Route.REVIEW and d.reasons == ["revised"]
