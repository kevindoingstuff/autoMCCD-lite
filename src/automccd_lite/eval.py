"""The mini evaluation harness: the provided set and the dlc set.

    uv run automccd-lite eval --set provided
    uv run automccd-lite eval --set dlc --only S01-ocr H01

provided  the 17 clean teaching scenarios, data/provided/<id>.json, each with
          its reference certificate (the gold answer from the client's deck)
dlc       40 harder records, data/dlc/<id>.json: the 34 damaged-record cases
          (OCR noise, cut pages, a mixed-in patient, ...) and the 6 coroner
          scenarios the pipeline must decline

Each case file holds the record in the POST /certify shape. The harness runs
certify_record in-process, so every row comes from the whole Result: the
rule check, both fixes, the codes. The numbers that matter: rules passed on
the first try and after the fix, the auto share, and the underlying cause
against the reference certificate coded through the same WHO tools. Every
case is also scored on behaviour: did the pipeline take the right action,
and did it avoid the outcomes the case forbids (must_not).

Writes runs/<set>_<hhmmss>Z/{results.json,summary.md}. Nothing is saved to
the store: the harness never calls save.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from automccd_lite import __version__
from automccd_lite.coding import CodeCache, WhoClient, WhoError, code_terms
from automccd_lite.config import DATA_DIR, ROOT, settings
from automccd_lite.draft import ModelUnavailable, system_prompt
from automccd_lite.models import Record, Result
from automccd_lite.pipeline import Deps, build_deps, certify_record
from automccd_lite.rules import verbatim_in

PROVIDED = DATA_DIR / "provided"
DLC = DATA_DIR / "dlc"
RUNS = ROOT / "runs"
SETS = ("provided", "dlc")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def load_reference(parent: str) -> dict | None:
    """The reference certificate (gold) of a provided scenario, or None."""
    path = PROVIDED / f"{parent}.json"
    return _read(path)["reference"] if path.exists() else None


# ---------------------------------------------------------------------------
# cases


@dataclass(frozen=True)
class EvalCase:
    """One record to certify and what is known about the right answer."""

    id: str
    parent: str
    variant: str
    source: str
    expected: str
    record: Record
    tags: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    intruder: str | None = None
    reference_chain: tuple[str, ...] | None = None  # Part I, 1a first
    reference_cause: str | None = None
    reference: dict | None = None  # the reference certificate, when the parent has one


def provided_cases() -> list[EvalCase]:
    """The 17 clean scenarios as `<id>-clean` records, each with its
    reference certificate."""
    out = []
    for path in sorted(PROVIDED.glob("*.json")):
        raw = _read(path)
        ref = raw["reference"]
        chain = tuple(c["certified_term"] for c in ref.get("part1") or [])
        out.append(
            EvalCase(
                id=raw["record"]["record_id"],
                parent=raw["id"],
                variant="clean",
                source="vs_scenarios",
                expected=raw["expected"],
                record=Record(**raw["record"]),
                tags=("clean",),
                reference_chain=chain or None,
                reference_cause=chain[-1] if chain else None,
                reference=ref,
            )
        )
    return out


def dlc_cases() -> list[EvalCase]:
    out = []
    for path in sorted(DLC.glob("*.json")):
        raw = _read(path)
        chain = raw.get("reference_chain")
        out.append(
            EvalCase(
                id=raw["id"],
                parent=raw["parent"],
                variant=raw["variant"],
                source=raw["source"],
                expected=raw["expected"],
                record=Record(**raw["record"]),
                tags=tuple(raw.get("tags") or ()),
                must_not=tuple(raw.get("must_not") or ()),
                forbidden_terms=tuple(raw.get("forbidden_terms") or ()),
                intruder=raw.get("intruder"),
                reference_chain=tuple(chain) if chain else None,
                reference_cause=raw.get("reference_cause"),
                reference=load_reference(raw["parent"])
                if raw["source"] == "vs_scenarios"
                else None,
            )
        )
    return out


def load_cases(set_name: str, only: tuple[str, ...] = ()) -> list[EvalCase]:
    """The cases of a set, in id order. `only` keeps case ids, parents or variants."""
    if set_name not in SETS:
        raise ValueError(f"unknown set {set_name!r}; one of {', '.join(SETS)}")
    cases = provided_cases() if set_name == "provided" else dlc_cases()
    if only:
        sel = set(only)
        cases = [c for c in cases if {c.id, c.parent, c.variant} & sel]
    return sorted(cases, key=lambda c: c.id)


# ---------------------------------------------------------------------------
# the reference, as a certificate


def reference_to_cert(reference: dict) -> dict:
    """Reference certificate -> the MCCD shape code_terms consumes."""

    def cond(c: dict) -> dict:
        iv = c.get("interval") or {}
        return {
            "certified_term": c.get("certified_term", ""),
            "interval": {
                "value": iv.get("value"),
                "unit": iv.get("unit"),
                "certainty": iv.get("certainty"),
            }
            if iv
            else None,
        }

    # part2 in the reference is a list of strings, sometimes carrying a
    # duration in parentheses; the term is what autocode needs.
    return {
        "part1": [cond(c) for c in reference.get("part1", [])],
        "part2": [
            {
                "certified_term": t.split("(")[0].strip()
                if isinstance(t, str)
                else t.get("certified_term", "")
            }
            for t in reference.get("part2") or []
        ],
    }


def chain_to_cert(chain: list[str] | tuple[str, ...]) -> dict:
    """A bare chain (1a first) -> the MCCD shape, no intervals."""
    return {"part1": [{"certified_term": t, "interval": None} for t in chain], "part2": []}


def chain_to_part1(forward_terms: list[str]) -> list[dict]:
    """Forward teaching chain -> certificate lines 1a..1d.

    The deck writes causation left-to-right (cause first). The certificate
    writes it top-to-bottom with the immediate cause on 1a and the originating
    cause on the lowest used line, i.e. reversed. A chain longer than four is
    collapsed by keeping the immediate and the originating cause.
    """
    lines = list(reversed(forward_terms))
    if len(lines) > 4:
        lines = lines[:3] + [lines[-1]]
    return [
        {"line": "1" + "abcd"[i], "certified_term": t, "interval": None}
        for i, t in enumerate(lines)
    ]


def norm(s: str) -> str:
    """Agreement between two certified terms folds case and punctuation away:
    the question is whether two certifiers wrote the same condition."""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class ReferenceCoder:
    """The reference underlying cause of each parent, coded once through the
    same autocode + DORIS calls as the model's certificate, so the comparison
    is code against code and not word against word."""

    def __init__(self, who: WhoClient, cache: CodeCache):
        self.who, self.cache = who, cache
        self.codes: dict[str, str | None] = {}

    def cause(self, case: EvalCase) -> str | None:
        if case.parent not in self.codes:
            mccd = (
                reference_to_cert(case.reference)
                if case.reference and case.reference.get("part1")
                else chain_to_cert(case.reference_chain or ())
            )
            try:
                coded = code_terms(mccd, self.cache, self.who, case.record.demographics)
                self.codes[case.parent] = coded.doris.get("ucod_stem_code")
            except WhoError:  # WhoUnavailable included
                self.codes[case.parent] = None
        return self.codes[case.parent]


def cause_match(reference: str | None, model: str | None) -> str | None:
    """exact | same_family | different, for two DORIS stem codes. Same
    family: the same code before the dot (BA41.0 and BA41.Z)."""
    if not reference or not model:
        return None
    if reference == model:
        return "exact"
    if reference.split(".")[0] == model.split(".")[0]:
        return "same_family"
    return "different"


# ---------------------------------------------------------------------------
# scoring


def right_action(expected: str, assessment: str | None, route: str | None) -> bool:
    if expected == "certifiable":
        return assessment == "certifiable"
    if expected == "insufficient_information":
        return assessment == "insufficient_information"
    if expected == "out_of_scope":
        return assessment == "out_of_scope"
    if expected == "certifiable_or_insufficient":
        return assessment in ("certifiable", "insufficient_information") and route != "auto"
    if expected == "out_of_scope_or_review":
        return assessment == "out_of_scope" or (assessment == "certifiable" and route != "auto")
    raise ValueError(expected)


def forbidden_outcomes(case: EvalCase, row: dict) -> list[str]:
    """Which of the case's must_not outcomes happened."""
    v = []
    terms = [t.casefold() for t in (row.get("model_chain") or []) + (row.get("model_part2") or [])]
    for m in case.must_not:
        if m == "auto" and row.get("route") == "auto":
            v.append("auto")
        elif m == "certify" and row.get("assessment") == "certifiable":
            v.append("certify")
        elif m == "mode_of_dying_1a" and "2" in (row.get("broken_rules") or []):
            v.append("mode_of_dying_1a")
        elif m == "explicit_without_record" and "explicit_diagnosis" in (
            row.get("model_bases") or []
        ):
            v.append("explicit_without_record")
        elif m == "negated_term" and any(f in t for f in case.forbidden_terms for t in terms):
            v.append("negated_term")
    # wrong_patient: rule 1 cannot tell the decedent's page from the intruder's,
    # because both sit in the same note. A span found only in the intruding
    # page is a certificate built on the wrong patient.
    if case.intruder and row.get("assessment") == "certifiable":
        own, _, other = case.record.fields.get("case_narrative", "").partition("[page 2]")
        if any(
            sp.strip() and not verbatim_in(sp, own) and verbatim_in(sp, other)
            for sp in row.get("spans") or []
        ):
            v.append("intruder_span")
    return v


def score(case: EvalCase, result: Result | None, err: str | None = None) -> dict[str, Any]:
    """The row for one case. `result` is None when the pipeline raised."""
    row: dict[str, Any] = {
        "id": case.id,
        "parent": case.parent,
        "variant": case.variant,
        "source": case.source,
        "tags": list(case.tags),
        "expected": case.expected,
    }
    if result is None:
        row.update(parsed=False, parse_error=err or "no reply", right_action=False)
        row["forbidden_outcomes"] = []
        return row
    row.update(
        route=result.route.value,
        reasons=list(result.reasons),
        usage=result.usage.model_dump(),
        timings_ms=dict(result.timings_ms),
        icd_error=result.icd_error,
    )
    v = result.rule_check
    if result.draft_status != "ok" or result.certificate is None or v is None:
        row.update(parsed=False, parse_error=result.draft_status, right_action=False)
        row["forbidden_outcomes"] = []
        return row
    mccd = result.certificate
    p1, p2 = mccd.get("part1") or [], mccd.get("part2") or []
    row.update(
        parsed=True,
        schema_ok=v.schema_ok,
        rules_passed=v.rules_passed,
        broken_rules=list(v.broken_rules),
        messages=list(v.messages),
        first_try=dict(v.before_fix)
        if v.before_fix
        else {
            "schema_ok": v.schema_ok,
            "rules_passed": v.rules_passed,
            "broken_rules": list(v.broken_rules),
        },
        rules_fixed=v.rules_fixed,
        fix_rejected=v.fix_rejected,
        terms_fixed=v.terms_fixed,
        term_changes=list(v.term_changes),
        assessment=v.assessment,
        confidence=v.confidence,
        flags=list(v.flags),
        note=(mccd.get("review") or {}).get("note"),
        model_chain=[c.get("certified_term", "") for c in p1],
        model_bases=[c.get("basis") for c in p1],
        model_part2=[c.get("certified_term", "") for c in p2],
        spans=[c.get("evidence_span") or "" for c in p1 + p2],
        reference_chain=list(case.reference_chain) if case.reference_chain else None,
        reference_cause=case.reference_cause,
    )
    if result.who_replies is not None:
        w = result.who_replies
        cu = w.codedit_cause or {}
        row.update(
            codes=list(w.codes.get("part1") or []),
            match_scores=list(w.match_scores),
            cause=w.doris.get("ucod_stem_code"),
            cause_full=w.doris.get("ucod_code"),
            quality_issues=list(w.codedit.get("issue_ids") or []),
            ill_defined=bool(cu.get("ill_defined")),
        )
    row["right_action"] = right_action(case.expected, v.assessment, row["route"])
    row["forbidden_outcomes"] = forbidden_outcomes(case, row)
    return row


def summarize(rows: list[dict]) -> dict[str, Any]:
    """The numbers that matter, plus the counts behind them."""
    parsed = [r for r in rows if r.get("parsed")]
    compared = [r for r in rows if r.get("cause_match")]
    n = len(rows)

    def share(k: int, d: int) -> float | None:
        return round(100 * k / d, 1) if d else None

    return {
        "n": n,
        "parsed": len(parsed),
        "rules_passed_first_try": share(
            sum(r["first_try"].get("rules_passed", False) for r in parsed), n
        ),
        "rules_passed_after_fix": share(sum(r["rules_passed"] for r in parsed), n),
        "rules_fixed": sum(bool(r.get("rules_fixed")) for r in parsed),
        "terms_fixed": sum(r.get("terms_fixed") == "applied" for r in parsed),
        "auto_share": share(sum(r.get("route") == "auto" for r in rows), n),
        "cause_matches_reference": share(
            sum(r["cause_match"] == "exact" for r in compared), len(compared)
        ),
        "cause_same_family": share(
            sum(r["cause_match"] in ("exact", "same_family") for r in compared), len(compared)
        ),
        "causes_compared": len(compared),
        "right_action": share(sum(bool(r.get("right_action")) for r in rows), n),
        "forbidden_outcomes": sum(len(r.get("forbidden_outcomes") or []) for r in rows),
        "broken_rules": dict(Counter(x for r in parsed for x in r["broken_rules"])),
        "routes": dict(Counter(r.get("route", "error") for r in rows)),
        "reasons": dict(Counter(x for r in rows for x in r.get("reasons") or [])),
        "ill_defined": sum(bool(r.get("ill_defined")) for r in parsed),
        "calls": sum((r.get("usage") or {}).get("calls", 0) for r in rows),
    }


# ---------------------------------------------------------------------------
# the run


def run(
    set_name: str,
    only: tuple[str, ...] = (),
    deps: Deps | None = None,
    out_dir: Path | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Certify every case of the set, score it, write the run folder.
    Returns {"run_dir", "manifest", "summary", "rows"}."""
    deps = deps or build_deps()
    cases = load_cases(set_name, only)
    started = datetime.now(UTC)
    run_dir = out_dir or RUNS / f"{set_name}_{started:%H%M%S}Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    reference = ReferenceCoder(deps.who, deps.cache)

    rows = []
    t0 = time.perf_counter()
    for i, case in enumerate(cases, 1):
        try:
            row = score(case, certify_record(case.record, deps))
        except ModelUnavailable as e:
            row = score(case, None, f"infra: {e}")
        if row.get("cause") and case.reference_chain:
            row["reference_cause_code"] = reference.cause(case)
            row["cause_match"] = cause_match(row["reference_cause_code"], row["cause"])
        rows.append(row)
        if not quiet:
            print(
                f"[{i:>2}/{len(cases)}] {case.id:<16} {row.get('route', 'error'):<7} "
                f"{','.join(row.get('broken_rules') or []) or '-':<8} "
                f"cause={row.get('cause') or '-':<8} {row.get('cause_match') or ''}",
                flush=True,
            )

    sp, cfg = system_prompt(), settings()
    manifest = {
        "run_id": run_dir.name,
        "created_utc": started.isoformat(timespec="seconds"),
        "set": set_name,
        "only": list(only),
        "method": "in-process certify_record, strict json_schema, the autocode cache file",
        "versions": {
            "prompt_version": sp.version,
            "prompt_sha1": sp.sha1,
            "schema_version": sp.schema_version,
            "model": cfg.draft.model,
            "icd_release": cfg.check.icd_release,
            "package_version": __version__,
        },
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    summary = summarize(rows)
    (run_dir / "results.json").write_text(
        json.dumps(
            {"manifest": manifest, "summary": summary, "cases": rows}, indent=2, ensure_ascii=False
        ),  # fmt: skip
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(report(manifest, summary, rows), encoding="utf-8")
    if not quiet:
        print(f"\n{run_dir / 'summary.md'}")
    return {"run_dir": run_dir, "manifest": manifest, "summary": summary, "rows": rows}


def report(manifest: dict, s: dict, rows: list[dict]) -> str:
    v = manifest["versions"]
    lines = [
        f"# Run `{manifest['run_id']}`",
        "",
        f"**Set** `{manifest['set']}` · {s['n']} cases · prompt `{v['prompt_version']}` "
        f"(sha1 `{v['prompt_sha1'][:10]}`) · model `{v['model']}` · ICD `{v['icd_release']}` "
        f"· calls {s['calls']} · wall {manifest['wall_s']} s",
        "",
        "| metric | value |",
        "|---|---|",
        f"| rules passed, first try | {s['rules_passed_first_try']}% |",
        f"| rules passed, after the fix | {s['rules_passed_after_fix']}% |",
        f"| rules fixed | {s['rules_fixed']} |",
        f"| terms fixed | {s['terms_fixed']} |",
        f"| auto share | {s['auto_share']}% |",
        f"| underlying cause matches the reference | {s['cause_matches_reference']}% "
        f"of {s['causes_compared']} |",
        f"| underlying cause in the same family | {s['cause_same_family']}% |",
        f"| right action | {s['right_action']}% |",
        f"| forbidden outcomes | {s['forbidden_outcomes']} |",
        f"| ill-defined underlying cause | {s['ill_defined']} |",
        "",
        f"Rules broken (final): {s['broken_rules'] or 'none'}. Routes: {s['routes']}.",
        "",
        "## Per case",
        "",
        "| case | expected | assessment | route | reasons | rules (first → final) "
        "| model cause | reference cause | match |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        fp = ",".join((r.get("first_try") or {}).get("broken_rules") or []) or "-"
        fin = ",".join(r.get("broken_rules") or []) or "-"
        lines.append(
            f"| {r['id']} | {r['expected']} | {r.get('assessment') or r.get('parse_error', '')} "
            f"| {r.get('route', '')} | {', '.join(r.get('reasons') or [])} | {fp} → {fin} "
            f"| {r.get('cause') or ''} | {r.get('reference_cause_code') or ''} "
            f"| {r.get('cause_match') or ''} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description="autoMCCD-lite mini evaluation harness")
    p.add_argument("--set", default="provided", choices=SETS)
    p.add_argument("--only", nargs="*", default=(), help="case ids, parents or variants")
    args = p.parse_args(argv)
    run(args.set, tuple(args.only))


if __name__ == "__main__":
    main()
