"""Shared fakes: a scripted model and a scripted coder, no network."""

from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from automccd_lite.coding import WhoCertificate
from automccd_lite.models import Draft, Note, Record, Usage

NARRATIVE = (
    "A male aged 49 years was admitted with fever and drowsiness. Staphylococcus aureus "
    "meningitis was confirmed on CSF culture. He developed septic shock and died on the "
    "fourth day. He was a diagnosed case of type II diabetes for the last 10 years."
)


def make_record(**over) -> Record:
    base = dict(
        record_id="T01",
        sex="male",
        age=49,
        age_unit="years",
        notes=[Note(name="case_narrative", text=NARRATIVE)],
    )
    base.update(over)
    return Record(**base)


def cond(term, span, value=2, unit="days", basis="explicit_diagnosis"):
    return {
        "certified_term": term,
        "evidence_field": "case_narrative",
        "evidence_span": span,
        "basis": basis,
        "interval": {"value": value, "unit": unit, "certainty": "documented"},
    }


def good_cert(record_id="T01") -> dict:
    return {
        "record_id": record_id,
        "assessment": "certifiable",
        "decline_reason": None,
        "part1": [
            cond("Septic shock", "septic shock", 1),
            cond("Staphylococcus aureus meningitis", "Staphylococcus aureus meningitis", 4),
        ],
        "part2": [cond("Type 2 diabetes mellitus", "type II diabetes", 10, "years")],
        "review": {"confidence": "high", "flags": [], "note": "Chain documented."},
    }


class ScriptedAsk:
    """Returns the scripted drafts in order; records every user message."""

    def __init__(self, drafts: Iterable[Draft | dict]):
        self.queue = [d if isinstance(d, Draft) else ok(d) for d in drafts]
        self.messages: list[str] = []

    def __call__(self, user: str) -> Draft:
        self.messages.append(user)
        if not self.queue:
            raise AssertionError("model asked more times than scripted")
        return self.queue.pop(0)


def ok(cert: dict) -> Draft:
    return Draft(status="ok", certificate=json.loads(json.dumps(cert)), model="fake",
                 usage=Usage(prompt_tokens=10, completion_tokens=5, calls=1))  # fmt: skip


class FakeIcd:
    """Autocode by table; DORIS picks the last coded Part I line; CoDEdit
    returns the issue ids you give it."""

    release = "test"

    def __init__(
        self, codes: dict[str, tuple[str | None, float | None]], issues=(), ucod_issues=()
    ):
        self.codes = codes
        self.issues = list(issues)
        self.ucod_issues = list(ucod_issues)
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def autocode(self, text: str) -> dict:
        self.calls.append(f"autocode:{text}")
        code, score = self.codes.get(text, (None, None))
        return {"query": text, "code": code, "matched_text": text, "match_score": score,
                "uri": None, "adjudicated": False}  # fmt: skip

    def doris(self, cert: WhoCertificate) -> dict:
        self.calls.append("doris")
        coded = [c for c in (cert.part1_codes or []) if c]
        ucod = coded[-1] if coded else None
        return {"ucod_code": ucod, "ucod_stem_code": ucod, "ucod_uri": None,
                "report": "fake", "rejected": False, "error": None, "warning": None}  # fmt: skip

    def codedit(self, cert: WhoCertificate) -> dict:
        self.calls.append("codedit")
        solo = len([c for c in (cert.part1_codes or []) if c]) == 1 and not cert.part2_codes
        ids = self.ucod_issues if solo and self.ucod_issues else self.issues
        return {
            "issue_ids": list(ids),
            "ill_defined": "FER-CE-931" in ids,
            "sex_inconsistent": False,
            "age_implausible": False,
            "blocked_by_validity": any(i.startswith("BER-") for i in ids),
            "report": "",
        }


GOOD_CODES = {
    "Septic shock": ("1G41", 0.95),
    "Staphylococcus aureus meningitis": ("1D01.0", 0.9),
    "Type 2 diabetes mellitus": ("5A11", 1.0),
}


@pytest.fixture
def record() -> Record:
    return make_record()


@pytest.fixture
def icd() -> FakeIcd:
    return FakeIcd(dict(GOOD_CODES))
