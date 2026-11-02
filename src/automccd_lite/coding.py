"""Step 2. Check, part two: code the certificate with the WHO's own tools.

code_terms: autocode each term -> DORIS (the underlying cause) -> CoDEdit on
the whole certificate -> CoDEdit on the underlying cause alone.

The client talks to the self-hosted WHO ICD-API container. Verified against
the running container, not inferred from docs:

  * No OAuth. The container answers unauthenticated.
  * DORIS and CoDEdit are both enabled by `enableDoris=true` and live at
    /icd/release/11/{releaseId}/doris and .../codedit.
  * DORIS takes CODES, not free text: causeOfDeathCodeA..E. Turning a
    written diagnosis into a code is the separate `autocode` endpoint.
  * The API accepts FIVE Part I lines (A..E), not the four of the WHO form.
  * CoDEdit returns machine-readable `issueIds`, a comma-separated string.
    A BER- validity error suppresses the clinical checks; see code_terms.
  * All three headers below are required: no Accept -> 412, no API-Version -> 400.

The second CoDEdit call asks whether the UNDERLYING cause is uninformative
and is immune to validity blocking: a BER- error on the whole certificate
suppresses the clinical checks, while the cause-alone call sends the single
valid stem code DORIS just returned.

The autocode cache codes each term once and reuses the entry. Autocode error
is indistinguishable from model error, so a stable code per term keeps runs
comparable, and the entry carries the matched text and score for audit.

fix_terms runs before coding when a Part I term scores under
check.min_match_score. It asks the model once for the standard ICD-11 name
of the same condition. Only the Part I certified_terms are taken over, and
each restated term is kept only if it codes with a HIGHER score than the
original: a fix may not make coding worse ("Rupture of ventricular wall"
scored 0.11 and coded to a congenital malformation). Calling cache.code() in
fix_terms IS the first autocode pass; code_terms reuses the entries.
"""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from automccd_lite.config import DATA_DIR
from automccd_lite.draft import Ask, render
from automccd_lite.models import Coding, Draft, Record, RuleCheck, iso_duration
from automccd_lite.rules import check_rules

HEADERS = {"API-Version": "v2", "Accept-Language": "en", "Accept": "application/json"}

# CoDEdit issue ids we name. Others are recorded verbatim and still route to review.
ILL_DEFINED = "FER-CE-931"
SEX_INCONSISTENT = ("FER-CE-873", "FER-CE-874")  # one id per direction
AGE_IMPLAUSIBLE = "FER-CE-913"

SEX = {"male": 1, "female": 2, "m": 1, "f": 2, "1": 1, "2": 2}

CACHE_PATH = DATA_DIR / "code_cache.json"


class WhoError(RuntimeError):
    """The API answered with an error (4xx, or 5xx after retries)."""


class WhoUnavailable(WhoError):
    """Container not reachable. The pipeline degrades to review."""


@dataclass
class WhoCertificate:
    """The subset of the death-certificate form these endpoints need."""

    sex: int | None = None
    estimated_age: str | None = None  # ISO-8601 duration, e.g. "P49Y"
    part1_codes: list[str | None] | None = None  # 1a first, up to five
    part1_intervals: list[str] | None = None
    part2_codes: list[str] | None = None

    def params(self) -> dict:
        p: dict = {"sex": self.sex, "estimatedAge": self.estimated_age}
        codes = self.part1_codes or []
        ivs = self.part1_intervals or []
        for i, letter in enumerate("ABCDE"):
            # A line autocode could not code is absent from the chain DORIS
            # reads, and so is its interval.
            if i < len(codes) and codes[i]:
                p[f"causeOfDeathCode{letter}"] = codes[i]
                if i < len(ivs) and ivs[i]:
                    p[f"interval{letter}"] = ivs[i]
        if self.part2_codes:
            p["causeOfDeathCodePart2"] = ",".join(c for c in self.part2_codes if c)
        return p


class WhoClient:
    def __init__(
        self,
        base_url: str,
        release: str,
        linearization: str = "mms",
        timeout: float = 30.0,
        retries: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.release = release
        self.linearization = linearization
        self.retries = retries
        self._http = httpx.Client(base_url=self.base_url, headers=HEADERS, timeout=timeout)

    def _get(self, path: str, params: dict | None = None, retries: int | None = None) -> dict:
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        retries = self.retries if retries is None else retries
        for attempt in range(retries + 1):
            try:
                r = self._http.get(path, params=clean)
            except httpx.TransportError as e:  # refused, timed out, reset while it loads
                if attempt == retries:
                    raise WhoUnavailable(f"{path}: {type(e).__name__}") from e
            else:
                if r.status_code < 400:
                    return r.json()
                # A 4xx is the API talking, not the container being absent. A
                # 5xx is the container struggling, and is retried like a timeout.
                if r.status_code < 500 or attempt == retries:
                    raise WhoError(f"{path}: HTTP {r.status_code}")
            time.sleep(1 + attempt)
        raise AssertionError("unreachable")

    def available(self) -> bool:
        try:
            self._get(f"/icd/release/11/{self.release}/{self.linearization}", retries=0)
            return True
        except WhoError:
            return False

    def autocode(self, text: str) -> dict:
        r = self._get(
            f"/icd/release/11/{self.release}/{self.linearization}/autocode",
            {"searchText": text},
        )
        return {
            "query": text,
            "code": r.get("theCode"),
            "matched_text": r.get("matchingText"),
            "match_score": r.get("matchScore"),
            "uri": r.get("foundationURI") or r.get("linearizationURI"),
            "adjudicated": False,
        }

    def doris(self, cert: WhoCertificate) -> dict:
        """Underlying-cause selection, with WHO's own rule trace."""
        r = self._get(f"/icd/release/11/{self.release}/doris", cert.params())
        return {
            "ucod_code": r.get("code"),
            "ucod_stem_code": r.get("stemCode"),
            "ucod_uri": r.get("stemURI"),
            "report": r.get("report"),
            "rejected": bool(r.get("reject")),
            "error": r.get("error"),
            "warning": r.get("warning"),
        }

    def codedit(self, cert: WhoCertificate) -> dict:
        """Quality checks. issueIds is an empty string when clean."""
        r = self._get(f"/icd/release/11/{self.release}/codedit", cert.params())
        raw = (r.get("issueIds") or "").replace(";", ",")
        ids = [i.strip() for i in raw.split(",") if i.strip()]
        return {
            "issue_ids": ids,
            "ill_defined": ILL_DEFINED in ids,
            "sex_inconsistent": any(i in ids for i in SEX_INCONSISTENT),
            "age_implausible": AGE_IMPLAUSIBLE in ids,
            # A validity error SUPPRESSES the clinical checks: a certificate
            # reading BER-CE-* has not been checked for ill-defined causes.
            "blocked_by_validity": any(i.startswith("BER-") for i in ids),
            "report": r.get("report") or "",
        }


# --------------------------------------------------------------------------
# The autocode cache
# --------------------------------------------------------------------------


def cache_key(text: str) -> str:
    """Case and whitespace do not change a code."""
    return " ".join(text.lower().split())


class CodeCache:
    """Each term coded once. With a path (the default, `data/code_cache.json`)
    the entries persist as a JSON file, `{term_key: {code, matched_text,
    match_score, ..., adjudicated}}`, shared by the api, the eval and the
    notebooks: a fresh clone codes the reference chains without the
    container, and a human can set `adjudicated: true` after review. The
    file is rewritten whole on every new term; a few hundred entries make
    that cheap. With `path=None` the entries live in memory only (tests)."""

    def __init__(self, client: WhoClient, path: Path | None = CACHE_PATH):
        self.client = client
        self.path = path
        self.data: dict[str, dict] = {}
        if path is not None and path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8") or "{}")

    def code(self, text: str) -> dict:
        key = cache_key(text)
        if key not in self.data:
            self.data[key] = self.client.autocode(text)
            if self.path is not None:
                self._save(self.path)
        return self.data[key]

    def _save(self, path: Path) -> None:
        # The api, the eval and the notebooks all write this file; merge what
        # another writer added since we loaded it, then rewrite.
        if path.exists():
            on_disk = json.loads(path.read_text(encoding="utf-8") or "{}")
            self.data = {**on_disk, **self.data}
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:  # LF on Windows too
            json.dump(self.data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)  # a crash mid-write leaves the old file intact


# --------------------------------------------------------------------------
# code_terms
# --------------------------------------------------------------------------


def who_certificate(cert: dict, cache: CodeCache, demographics: dict | None) -> WhoCertificate:
    """Map a model-produced MCCD onto the API's certificate shape."""
    p1 = [c.get("certified_term", "") for c in (cert.get("part1") or [])]
    p2 = [c.get("certified_term", "") for c in (cert.get("part2") or [])]
    intervals = []
    for c in cert.get("part1") or []:
        iv = c.get("interval") or {}
        intervals.append(iso_duration(iv.get("value"), iv.get("unit")))
    demo = demographics or {}
    return WhoCertificate(
        sex=SEX.get(str(demo.get("sex") or "").lower()),
        estimated_age=demo.get("age_iso"),
        part1_codes=[cache.code(t)["code"] if t else None for t in p1],
        part1_intervals=intervals,
        part2_codes=[cache.code(t)["code"] for t in p2 if t],
    )


def code_terms(
    cert: dict, cache: CodeCache, client: WhoClient, demographics: dict | None
) -> Coding:
    c = who_certificate(cert, cache, demographics)
    p1_terms = [x.get("certified_term", "") for x in (cert.get("part1") or [])]
    p2_terms = [x.get("certified_term", "") for x in (cert.get("part2") or [])]
    entries = [cache.code(t) for t in p1_terms + p2_terms if t]
    doris = client.doris(c)
    codedit = client.codedit(c)
    ucod = doris.get("ucod_stem_code")
    codedit_cause = (
        client.codedit(WhoCertificate(sex=c.sex, estimated_age=c.estimated_age, part1_codes=[ucod]))
        if ucod
        else None
    )
    return Coding(
        codes={"part1": c.part1_codes or [], "part2": list(c.part2_codes or [])},
        uncoded_part1=[i for i, code in enumerate(c.part1_codes or []) if not code],
        match_scores=[cache.code(t)["match_score"] if t else None for t in p1_terms],
        autocode=entries,
        doris=doris,
        codedit=codedit,
        codedit_cause=codedit_cause,
    )


# --------------------------------------------------------------------------
# fix_terms
# --------------------------------------------------------------------------

FIX_TERMS_TEMPLATE = """{record}

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


def fix_terms_message(record: Record, cert: dict, low: list[tuple[str, dict]]) -> str:
    terms = "\n".join(
        f"- '{t}' (closest ICD-11 match: '{e.get('matched_text')}', score {e.get('match_score')})"
        for t, e in low
    )
    return FIX_TERMS_TEMPLATE.format(
        record=render(record), terms=terms, previous=json.dumps(cert, ensure_ascii=False)
    )


def scoped_to_terms(old: dict, new: dict) -> dict | None:
    """The old certificate with only the fix's Part I certified_terms
    taken over: Part II and every other Part I field stay as they were.
    None when the fix changed the number of Part I lines."""
    old_p1, new_p1 = old.get("part1") or [], new.get("part1") or []
    if len(old_p1) != len(new_p1):
        return None
    scoped = copy.deepcopy(old)
    for line, line_new in zip(scoped.get("part1") or [], new_p1, strict=True):
        if "certified_term" in line_new:
            line["certified_term"] = line_new["certified_term"]
    return scoped


def fix_terms(
    record: Record,
    draft: Draft,
    validation: RuleCheck,
    cache: CodeCache,
    ask: Ask,
    min_score: float,
) -> tuple[Draft, RuleCheck]:
    """The (draft, check) the pipeline goes on with. Unchanged when no
    Part I term scores low."""
    cert = draft.certificate or {}
    low = low_score_terms(cert, cache, min_score)
    if not low:
        return draft, validation

    again = ask(fix_terms_message(record, cert, low))
    total = draft.usage + again.usage
    new = again.certificate
    new_v = check_rules(new, record.fields) if new is not None else None
    if (
        new is None
        or new_v is None
        or not new_v.rules_passed
        or new_v.assessment != validation.assessment
        or len(new_v.model_chain) != len(validation.model_chain)
    ):
        validation.terms_fixed = "rejected"
        draft.usage = total
        return draft, validation

    new = scoped_to_terms(cert, new)
    if new is None:
        validation.terms_fixed = "rejected"
        draft.usage = total
        return draft, validation
    again.certificate = new
    new_v = check_rules(new, record.fields)

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
        new_v = check_rules(new, record.fields)
        new_v.term_reverted = reverted
    changes = [
        [x, y] for x, y in zip(validation.model_chain, new_v.model_chain, strict=False) if x != y
    ]
    if not changes:
        validation.term_reverted = reverted
        validation.terms_fixed = "unchanged"
        draft.usage = total
        return draft, validation

    new_v.rules_fixed, new_v.before_fix = validation.rules_fixed, validation.before_fix
    new_v.fix_rejected = validation.fix_rejected
    new_v.terms_fixed = "applied"
    new_v.term_changes = changes
    again.usage = total
    return again, new_v
