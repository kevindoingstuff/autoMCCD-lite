"""The gold corpus loads, and the four gold checks G1-G4.

The forward-chain inversion is the single most dangerous step in the gold
corpus: if it were backwards, every score on 10 of the 17 headline cases
would be wrong and nothing would crash. G1-G4 keep that failure loud.
"""

from __future__ import annotations

import pytest

from automccd_lite.eval import (
    GOLD,
    age_from_iso,
    chain_to_part1,
    gold_to_cert,
    load_cases,
    load_gold,
    norm,
    scenarios,
    sets,
)
from automccd_lite.rules import UNIT_HOURS

# Underlying causes stated in prose on the answer slides.
STATED_UCOD = {
    "S02": "arteriosclerosis",
    "S05": "cerebrovascular accident",
    "S11": "carcinoma of the sigmoid colon",
}


def test_sets_are_frozen_as_documented():
    s = sets()
    assert len(s["headline"]) == 17 and "S05" not in s["headline"]
    assert len(s["held_out_out_of_scope"]) == 6


def test_load_cases_counts():
    headline = load_cases("headline")
    assert [c.id for c in headline] == [f"{p}-clean" for p in sets()["headline"]]
    assert all(c.expected == "certifiable" and c.gold_chain for c in headline)
    robust = load_cases("robust")
    assert len(robust) == 34 and all("stress" in c.tags for c in robust)
    assert [c.id for c in load_cases("robust", ("S01",))][:1] == ["S01-clean"]
    with pytest.raises(ValueError):
        load_cases("all")


@pytest.mark.parametrize(
    ("iso", "want"),
    [("P49Y", (49.0, "years")), ("P45D", (45.0, "days")), ("P3M", (3.0, "months")),
     ("PT2M", (2.0, "minutes")), ("PT6H", (6.0, "hours")), (None, (None, None))],
)  # fmt: skip
def test_age_from_iso(iso, want):
    assert age_from_iso(iso) == want


def test_g1_s01_round_trip():
    """S01 has both a text chain and a certificate image; the text-derived
    Part I must equal the image-derived Part I, term for term, in order."""
    s1 = load_gold("S01")
    derived = [
        norm(line["certified_term"]) for line in chain_to_part1(s1["text_sequence_forward_chain"])
    ]
    assert derived == [norm(line["certified_term"]) for line in s1["part1"]]


@pytest.mark.parametrize(("cid", "stated"), sorted(STATED_UCOD.items()))
def test_g2_stated_ucod_is_the_lowest_used_line(cid, stated):
    g = load_gold(cid)
    assert norm(stated) in norm(g["part1"][-1]["certified_term"])


def test_g3_gold_intervals_do_not_decrease_down_the_chain():
    for path in sorted(GOLD.glob("*.json")):
        g = load_gold(path.stem)
        if not g.get("has_intervals"):
            continue
        prev = None
        for line in g["part1"]:
            iv = line.get("interval") or {}
            if iv.get("value") is None or iv.get("unit") not in UNIT_HOURS:
                continue
            hours = iv["value"] * UNIT_HOURS[iv["unit"]]
            assert prev is None or hours >= prev, f"{g['id']}: {line['certified_term']}"
            prev = hours


def test_g4_every_in_scope_case_has_gold_part1():
    in_scope = [s["id"] for s in scenarios().values() if s["in_scope"]]
    assert len(in_scope) == 18
    for cid in in_scope:
        assert load_gold(cid)["part1"], cid


def test_gold_to_cert_shapes():
    c = gold_to_cert(load_gold("S01"))
    assert [x["certified_term"] for x in c["part1"]][0] == "Septic shock"
    assert c["part1"][0]["interval"] == {"value": 2, "unit": "days", "certainty": "documented"}
    assert c["part2"][0]["certified_term"] == "Diabetes Mellitus"
