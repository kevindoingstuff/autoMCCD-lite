"""The mini evaluation harness: the 17 headline cases and the robust sample.

    python -m automccd_lite.eval --set headline        (or: make eval)
    python -m automccd_lite.eval --set robust --only S01-ocr H01

Runs `certify_record` in-process, so every row comes from the full
Result: the gate's verdict, both revisions, the codes. Four numbers
matter: validator-clean on the first pass and after revision, the auto share,
and the UCOD against the gold certificate coded through the same
autocode + DORIS pipeline. The robust sample is scored on behaviour only:
did the pipeline certify or decline as the case expects, and did it respect
the case's must_not list.

Writes runs/<set>_<hhmmss>Z/{results.json,summary.md}. Nothing goes to
Postgres or Blob: the harness passes neither to the pipeline.
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
from automccd_lite.code import CodeCache, WhoClient, WhoError, WhoUnavailable, code_draft
from automccd_lite.config import DATA_DIR, ROOT, settings
from automccd_lite.draft import ModelUnavailable, connect_model
from automccd_lite.models import Record, Result
from automccd_lite.pipeline import Deps, certify_record
from automccd_lite.prompt import system_prompt
from automccd_lite.validate import verbatim_in

GOLD = DATA_DIR / "gold"
RUNS = ROOT / "runs"
SETS = ("headline", "robust")


def _read(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@cache
def sets() -> dict[str, list[str]]:
    return {k: v for k, v in _read("eval_sets.json").items() if not k.startswith("_")}


@cache
def scenarios() -> dict[str, dict]:
    return {s["id"]: s for s in _read("scenarios.json")}


@cache
def demographics() -> dict[str, dict]:
    return _read("demographics.json")


@cache
def load_gold(parent: str) -> dict | None:
    path = GOLD / f"{parent}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@cache
def robust_sample() -> dict[str, Any]:
    return _read("robust_sample.json")


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
    gold_chain: tuple[str, ...] | None = None  # Part I, 1a first
    ref_ucod: str | None = None
    gold: dict | None = None  # the gold file (intervals, Part II), when the parent has one


_ISO_AGE = re.compile(
    r"^P(?:T(?P<tv>\d+(?:\.\d+)?)(?P<tu>[HM])|(?P<v>\d+(?:\.\d+)?)(?P<u>[YMWD]))$"
)
_UNITS = {"H": "hours", "TM": "minutes", "Y": "years", "M": "months", "W": "weeks", "D": "days"}


def age_from_iso(iso: str | None) -> tuple[float | None, str | None]:
    """'P49Y' -> (49, 'years'); the inverse of models.iso_duration. Note the
    T: P2M is two months, PT2M two minutes."""
    m = _ISO_AGE.match(iso or "")
    if not m:
        return None, None
    if m.group("tv"):
        unit = "T" + m.group("tu") if m.group("tu") == "M" else m.group("tu")
        return float(m.group("tv")), _UNITS[unit]
    return float(m.group("v")), _UNITS[m.group("u")]


def headline_cases() -> list[EvalCase]:
    """The 17 headline scenarios as `<parent>-clean` records, the same ids
    the full autoMCCD harness uses, so the two runs compare case by case."""
    out = []
    for parent in sets()["headline"]:
        s, d, g = scenarios()[parent], demographics()[parent], load_gold(parent) or {}
        age, unit = age_from_iso(d.get("age_iso"))
        record = Record(
            record_id=f"{parent}-clean",
            sex=d.get("sex"),
            age=age,
            age_unit=unit,
            notes=[{"name": "case_narrative", "text": s["narrative"]}],
        )
        chain = tuple(c["certified_term"] for c in g.get("part1") or [])
        out.append(
            EvalCase(
                id=record.record_id,
                parent=parent,
                variant="clean",
                source="vs_scenarios",
                expected="certifiable",
                record=record,
                tags=("clean",),
                gold_chain=chain or None,
                ref_ucod=chain[-1] if chain else None,
                gold=g or None,
            )
        )
    return out


def robust_cases() -> list[EvalCase]:
    out = []
    for raw in robust_sample()["cases"]:
        age, unit = age_from_iso(raw.get("age_iso"))
        record = Record(
            record_id=raw["id"],
            sex=raw.get("sex"),
            age=age,
            age_unit=unit,
            registry_info=raw.get("context"),
            notes=[{"name": n, "text": t} for n, t in raw["fields"].items()],
        )
        out.append(
            EvalCase(
                id=raw["id"],
                parent=raw["parent"],
                variant=raw["variant"],
                source=raw["source"],
                expected=raw["expected"],
                record=record,
                tags=tuple(raw.get("tags") or ()),
                must_not=tuple(raw.get("must_not") or ()),
                forbidden_terms=tuple(raw.get("forbidden_terms") or ()),
                intruder=raw.get("intruder"),
                gold_chain=tuple(raw["gold_chain"]) if raw.get("gold_chain") else None,
                ref_ucod=raw.get("ref_ucod"),
                gold=load_gold(raw["parent"]) if raw["source"] == "vs_scenarios" else None,
            )
        )
    return out


def load_cases(set_name: str, only: tuple[str, ...] = ()) -> list[EvalCase]:
    """The cases of a set, in id order. `only` keeps case ids, parents or variants."""
    if set_name not in SETS:
        raise ValueError(f"unknown set {set_name!r}; one of {', '.join(SETS)}")
    cases = headline_cases() if set_name == "headline" else robust_cases()
    if only:
        sel = set(only)
        cases = [c for c in cases if {c.id, c.parent, c.variant} & sel]
    return sorted(cases, key=lambda c: c.id)


# ---------------------------------------------------------------------------
# the gold, as a certificate


def gold_to_cert(gold: dict) -> dict:
    """Gold certificate -> the MCCD shape code_draft consumes."""

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

    # part2 in gold is a list of strings, sometimes carrying a duration in
    # parentheses; the term is what autocode needs.
    return {
        "part1": [cond(c) for c in gold.get("part1", [])],
        "part2": [
            {
                "certified_term": t.split("(")[0].strip()
                if isinstance(t, str)
                else t.get("certified_term", "")
            }
            for t in gold.get("part2") or []
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


class Reference:
    """The gold UCOD of each parent, coded once through the same autocode +
    DORIS pipeline as the model's certificate, so the comparison is code
    against code and not word against word."""

    def __init__(self, icd: WhoClient, cache: CodeCache):
        self.icd, self.cache = icd, cache
        self.codes: dict[str, str | None] = {}

    def ucod(self, case: EvalCase) -> str | None:
        if case.parent not in self.codes:
            mccd = (
                gold_to_cert(case.gold)
                if case.gold and case.gold.get("part1")
                else chain_to_cert(case.gold_chain or ())
            )
            try:
                coded = code_draft(mccd, self.cache, self.icd, case.record.demographics)
                self.codes[case.parent] = coded.doris.get("ucod_stem_code")
            except (WhoError, WhoUnavailable):
                self.codes[case.parent] = None
        return self.codes[case.parent]


def ucod_match(ref: str | None, model: str | None) -> str | None:
    """exact | same_stem | different, for two DORIS stem codes."""
    if not ref or not model:
        return None
    if ref == model:
        return "exact"
    if ref.split(".")[0] == model.split(".")[0]:
        return "same_stem"
    return "different"


# ---------------------------------------------------------------------------
# scoring


def behaviour_ok(expected: str, assessment: str | None, route: str | None) -> bool:
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


def must_not_violations(case: EvalCase, row: dict) -> list[str]:
    v = []
    terms = [t.casefold() for t in (row.get("model_chain") or []) + (row.get("model_part2") or [])]
    for m in case.must_not:
        if m == "auto" and row.get("route") == "auto":
            v.append("auto")
        elif m == "certify" and row.get("assessment") == "certifiable":
            v.append("certify")
        elif m == "mode_of_dying_1a" and "V2" in (row.get("broken_rules") or []):
            v.append("mode_of_dying_1a")
        elif m == "explicit_without_record" and "explicit_diagnosis" in (
            row.get("model_bases") or []
        ):
            v.append("explicit_without_record")
        elif m == "negated_term" and any(f in t for f in case.forbidden_terms for t in terms):
            v.append("negated_term")
    # wrong_patient: V1 cannot tell the decedent's page from the intruder's,
    # because both sit in the same field. A span found only in the intruding
    # page is a certificate built on the wrong patient.
    if case.intruder and row.get("assessment") == "certifiable":
        own, _, other = case.record.fields.get("case_narrative", "").partition("[page 2]")
        if any(
            sp.strip() and not verbatim_in(sp, own) and verbatim_in(sp, other)
            for sp in row.get("spans") or []
        ):
            v.append("intruder_span")
    return v


def score(case: EvalCase, cert: Result | None, err: str | None = None) -> dict[str, Any]:
    """The row for one case. `cert` is None when the pipeline raised."""
    row: dict[str, Any] = {
        "id": case.id,
        "parent": case.parent,
        "variant": case.variant,
        "source": case.source,
        "tags": list(case.tags),
        "expected": case.expected,
    }
    if cert is None:
        row.update(parsed=False, parse_error=err or "no reply", behaviour_ok=False)
        row["must_not_violations"] = []
        return row
    row.update(
        route=cert.route.value,
        reasons=list(cert.reasons),
        usage=cert.usage.model_dump(),
        timings_ms=dict(cert.timings_ms),
        icd_error=cert.icd_error,
    )
    v = cert.validation
    if cert.draft_status != "ok" or cert.certificate is None or v is None:
        row.update(parsed=False, parse_error=cert.draft_status, behaviour_ok=False)
        row["must_not_violations"] = []
        return row
    mccd = cert.certificate
    p1, p2 = mccd.get("part1") or [], mccd.get("part2") or []
    row.update(
        parsed=True,
        schema_ok=v.schema_ok,
        rules_passed=v.rules_passed,
        messages=list(v.messages),
        broken_rules=list(v.broken_rules),
        first_pass=dict(v.before_fix)
        if v.before_fix
        else {
            "schema_ok": v.schema_ok,
            "rules_passed": v.rules_passed,
            "broken_rules": list(v.broken_rules),
        },
        repaired=v.rules_fixed,
        repair_rejected=v.fix_rejected,
        term_repair=v.terms_fixed,
        term_changes=list(v.term_changes),
        assessment=v.assessment,
        confidence=v.confidence,
        flags=list(v.flags),
        note=(mccd.get("review") or {}).get("note"),
        model_chain=[c.get("certified_term", "") for c in p1],
        model_bases=[c.get("basis") for c in p1],
        model_part2=[c.get("certified_term", "") for c in p2],
        spans=[c.get("evidence_span") or "" for c in p1 + p2],
        gold_chain=list(case.gold_chain) if case.gold_chain else None,
        ref_ucod=case.ref_ucod,
    )
    if cert.coded is not None:
        cu = cert.coded.codedit_cause or {}
        row.update(
            codes=list(cert.coded.codes.get("part1") or []),
            match_scores=list(cert.coded.match_scores),
            ucod_code=cert.coded.doris.get("ucod_stem_code"),
            underlying_cause_full=cert.coded.doris.get("ucod_code"),
            quality_issues=list(cert.coded.codedit.get("issue_ids") or []),
            ill_defined=bool(cu.get("ill_defined")),
        )
    row["behaviour_ok"] = behaviour_ok(case.expected, v.assessment, row["route"])
    row["must_not_violations"] = must_not_violations(case, row)
    return row


def summarize(rows: list[dict]) -> dict[str, Any]:
    """The four numbers, plus the counts behind them."""
    parsed = [r for r in rows if r.get("parsed")]
    gold = [r for r in rows if r.get("ucod_code_match")]
    n = len(rows)

    def share(k: int, d: int) -> float | None:
        return round(100 * k / d, 1) if d else None

    return {
        "n": n,
        "parsed": len(parsed),
        "validator_clean_first_pass": share(
            sum(r["first_pass"].get("rules_passed", False) for r in parsed), n
        ),
        "validator_clean": share(sum(r["rules_passed"] for r in parsed), n),
        "revised": sum(bool(r.get("repaired")) for r in parsed),
        "terms_revised": sum(r.get("term_repair") == "applied" for r in parsed),
        "auto_share": share(sum(r.get("route") == "auto" for r in rows), n),
        "gold_ucod_exact": share(sum(r["ucod_code_match"] == "exact" for r in gold), len(gold)),
        "gold_ucod_same_stem_or_better": share(
            sum(r["ucod_code_match"] in ("exact", "same_stem") for r in gold), len(gold)
        ),
        "gold_compared": len(gold),
        "behaviour_ok": share(sum(bool(r.get("behaviour_ok")) for r in rows), n),
        "must_not_violations": sum(len(r.get("must_not_violations") or []) for r in rows),
        "broken_rules": dict(Counter(x for r in parsed for x in r["broken_rules"])),
        "routes": dict(Counter(r.get("route", "error") for r in rows)),
        "reasons": dict(Counter(x for r in rows for x in r.get("reasons") or [])),
        "ill_defined": sum(bool(r.get("ill_defined")) for r in parsed),
        "calls": sum((r.get("usage") or {}).get("calls", 0) for r in rows),
    }


# ---------------------------------------------------------------------------
# the run


def default_deps() -> Deps:
    cfg = settings()
    icd = WhoClient(
        cfg.env.icd_api_url,
        cfg.code.icd_release,
        cfg.code.linearization,
        cfg.code.timeout_s,
        cfg.code.max_retries,
    )
    return Deps(ask=connect_model(cfg), icd=icd, cache=CodeCache(icd))


def run(
    set_name: str,
    only: tuple[str, ...] = (),
    deps: Deps | None = None,
    out_dir: Path | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Certify every case of the set, score it, write the run folder.
    Returns {"run_dir", "manifest", "summary", "rows"}."""
    deps = deps or default_deps()
    cases = load_cases(set_name, only)
    started = datetime.now(UTC)
    run_dir = out_dir or RUNS / f"{set_name}_{started:%H%M%S}Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    reference = Reference(deps.icd, deps.cache)  # type: ignore[arg-type]

    rows = []
    t0 = time.perf_counter()
    for i, case in enumerate(cases, 1):
        try:
            cert = certify_record(case.record, deps)
            row = score(case, cert)
        except ModelUnavailable as e:
            row = score(case, None, f"infra: {e}")
        if row.get("ucod_code") and case.gold_chain:
            row["gold_ucod_code"] = reference.ucod(case)
            row["ucod_code_match"] = ucod_match(row["gold_ucod_code"], row["ucod_code"])
        rows.append(row)
        if not quiet:
            print(
                f"[{i:>2}/{len(cases)}] {case.id:<16} {row.get('route', 'error'):<7} "
                f"{','.join(row.get('broken_rules') or []) or '-':<8} "
                f"ucod={row.get('ucod_code') or '-':<8} {row.get('ucod_code_match') or ''}",
                flush=True,
            )

    sp, cfg = system_prompt(), settings()
    manifest = {
        "run_id": run_dir.name,
        "created_utc": started.isoformat(timespec="seconds"),
        "set": set_name,
        "only": list(only),
        "corpus_version": robust_sample()["corpus_version"],
        "method": "in-process certify_record, strict json_schema, the autocode cache file",
        "versions": {
            "prompt_version": sp.version,
            "prompt_sha1": sp.sha1,
            "schema_version": sp.schema_version,
            "model": cfg.draft.model,
            "icd_release": cfg.code.icd_release,
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
        f"| validator clean, first pass | {s['validator_clean_first_pass']}% |",
        f"| validator clean, final | {s['validator_clean']}% |",
        f"| revised once | {s['revised']} |",
        f"| terminology revised | {s['terms_revised']} |",
        f"| auto share | {s['auto_share']}% |",
        f"| UCOD exact vs gold | {s['gold_ucod_exact']}% of {s['gold_compared']} |",
        f"| UCOD same stem or better | {s['gold_ucod_same_stem_or_better']}% |",
        f"| behaviour as expected | {s['behaviour_ok']}% |",
        f"| must_not violations | {s['must_not_violations']} |",
        f"| ill-defined UCOD | {s['ill_defined']} |",
        "",
        f"Rules fired (final): {s['broken_rules'] or 'none'}. Routes: {s['routes']}.",
        "",
        "## Per case",
        "",
        "| case | expected | assessment | route | reasons | rules (first → final) "
        "| model UCOD | gold UCOD | match |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        fp = ",".join((r.get("first_pass") or {}).get("broken_rules") or []) or "-"
        fin = ",".join(r.get("broken_rules") or []) or "-"
        lines.append(
            f"| {r['id']} | {r['expected']} | {r.get('assessment') or r.get('parse_error', '')} "
            f"| {r.get('route', '')} | {', '.join(r.get('reasons') or [])} | {fp} → {fin} "
            f"| {r.get('ucod_code') or ''} | {r.get('gold_ucod_code') or ''} "
            f"| {r.get('ucod_code_match') or ''} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description="autoMCCD-lite mini evaluation harness")
    p.add_argument("--set", default="headline", choices=SETS)
    p.add_argument("--only", nargs="*", default=(), help="case ids, parents or variants")
    args = p.parse_args(argv)
    run(args.set, tuple(args.only))


if __name__ == "__main__":
    main()
