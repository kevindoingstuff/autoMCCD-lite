"""The case files load, and the four reference checks G1-G4.

The forward-chain inversion is the single most dangerous step in the
reference certificates: if it were backwards, every score on 10 of the 17
provided cases would be wrong and nothing would crash. G1-G4 keep that
failure loud.
"""

from __future__ import annotations

import pytest

from automccd_lite.eval import (
    PROVIDED,
    chain_to_part1,
    load_cases,
    load_reference,
    norm,
    reference_to_cert,
)
from automccd_lite.rules import UNIT_HOURS

# Underlying causes stated in prose on the answer slides.
STATED_CAUSE = {
    "S02": "arteriosclerosis",
    "S11": "carcinoma of the sigmoid colon",
}


def test_sets_have_the_documented_counts():
    provided = load_cases("provided")
    assert len(provided) == 17 and "S05-clean" not in {c.id for c in provided}
    dlc = load_cases("dlc")
    assert len(dlc) == 40
    assert sum("stress" in c.tags for c in dlc) == 34
    coroner = [c for c in dlc if "coroner" in c.tags]
    assert [c.parent for c in coroner] == ["S19", "S20", "S21", "S22", "S23", "S24"]
    assert all(c.expected == "out_of_scope" for c in coroner)


def test_load_cases():
    provided = load_cases("provided")
    assert [c.id for c in provided][:2] == ["S01-clean", "S02-clean"]
    assert all(c.expected == "certifiable" and c.reference_chain for c in provided)
    assert [c.id for c in load_cases("dlc", ("S01",))][:1] == ["S01-clean"]
    with pytest.raises(ValueError):
        load_cases("all")


def test_g1_s01_round_trip():
    """S01 has both a text chain and a certificate image; the text-derived
    Part I must equal the image-derived Part I, term for term, in order."""
    s1 = load_reference("S01")
    derived = [
        norm(line["certified_term"]) for line in chain_to_part1(s1["text_sequence_forward_chain"])
    ]
    assert derived == [norm(line["certified_term"]) for line in s1["part1"]]


@pytest.mark.parametrize(("cid", "stated"), sorted(STATED_CAUSE.items()))
def test_g2_stated_cause_is_the_lowest_used_line(cid, stated):
    ref = load_reference(cid)
    assert norm(stated) in norm(ref["part1"][-1]["certified_term"])


def test_g3_reference_intervals_do_not_decrease_down_the_chain():
    for path in sorted(PROVIDED.glob("*.json")):
        ref = load_reference(path.stem)
        if not ref.get("has_intervals"):
            continue
        prev = None
        for line in ref["part1"]:
            iv = line.get("interval") or {}
            if iv.get("value") is None or iv.get("unit") not in UNIT_HOURS:
                continue
            hours = iv["value"] * UNIT_HOURS[iv["unit"]]
            assert prev is None or hours >= prev, f"{path.stem}: {line['certified_term']}"
            prev = hours


def test_g4_every_provided_case_has_a_reference_part1():
    paths = sorted(PROVIDED.glob("*.json"))
    assert len(paths) == 17
    for path in paths:
        assert load_reference(path.stem)["part1"], path.stem


def test_reference_to_cert_shapes():
    c = reference_to_cert(load_reference("S01"))
    assert [x["certified_term"] for x in c["part1"]][0] == "Septic shock"
    assert c["part1"][0]["interval"] == {"value": 2, "unit": "days", "certainty": "documented"}
    assert c["part2"][0]["certified_term"] == "Diabetes Mellitus"
