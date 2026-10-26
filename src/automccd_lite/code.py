"""code_draft: code the certificate with WHO's tools.

autocode -> DORIS -> CoDEdit (whole certificate) -> CoDEdit (UCOD alone).

The client talks to the self-hosted WHO ICD-API container. Verified against
the running container, not inferred from docs:

  * No OAuth. The container answers unauthenticated.
  * DORIS and CoDEdit are both enabled by `enableDoris=true` and live at
    /icd/release/11/{releaseId}/doris and .../codedit.
  * DORIS takes CODES, not free text: causeOfDeathCodeA..E. Turning a
    written diagnosis into a code is the separate `autocode` endpoint.
  * The API accepts FIVE Part I lines (A..E), not the four of the WHO form.
  * CoDEdit returns machine-readable `issueIds`, a comma-separated string.
    A BER- validity error suppresses the clinical checks; see code_draft.
  * All three headers below are required: no Accept -> 412, no API-Version -> 400.

The second CoDEdit call asks the question the metric names (is the
UNDERLYING cause uninformative) and is immune to validity blocking: a BER-
error on the whole certificate suppresses the clinical checks, while the
UCOD-alone call sends a single valid stem code DORIS just returned.

The autocode cache codes each term once and reuses the entry. Autocode error
is indistinguishable from model error, so a stable code per term keeps runs
comparable, and the entry carries the matched text and score for audit.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from automccd_lite.config import DATA_DIR
from automccd_lite.models import CodedDraft, iso_duration

HEADERS = {"API-Version": "v2", "Accept-Language": "en", "Accept": "application/json"}

# CoDEdit issue ids we name. Others are recorded verbatim and still route to review.
ILL_DEFINED = "FER-CE-931"
SEX_INCONSISTENT = ("FER-CE-873", "FER-CE-874")  # one id per direction
AGE_IMPLAUSIBLE = "FER-CE-913"

SEX = {"male": 1, "female": 2, "m": 1, "f": 2, "1": 1, "2": 2}

CACHE_PATH = DATA_DIR / "code_cache.json"


class IcdError(RuntimeError):
    """The API answered with an error (4xx, or 5xx after retries)."""


class ICDUnavailable(IcdError):
    """Container not reachable. The pipeline degrades to review."""


@dataclass
class IcdCertificate:
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


class IcdClient:
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
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                if attempt == retries:
                    raise ICDUnavailable(f"{path}: {type(e).__name__}") from e
            else:
                if r.status_code < 400:
                    return r.json()
                # A 4xx is the API talking, not the container being absent. A
                # 5xx is the container struggling, and is retried like a timeout.
                if r.status_code < 500 or attempt == retries:
                    raise IcdError(f"{path}: HTTP {r.status_code}")
            time.sleep(1 + attempt)
        raise AssertionError("unreachable")

    def available(self) -> bool:
        try:
            self._get(f"/icd/release/11/{self.release}/{self.linearization}", retries=0)
            return True
        except IcdError:
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

    def doris(self, cert: IcdCertificate) -> dict:
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

    def codedit(self, cert: IcdCertificate) -> dict:
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


class CodeCache(Protocol):
    def code(self, text: str) -> dict: ...


class MemoryCodeCache:
    def __init__(self, client: IcdClient):
        self.client = client
        self.data: dict[str, dict] = {}

    def code(self, text: str) -> dict:
        key = cache_key(text)
        if key not in self.data:
            self.data[key] = self.client.autocode(text)
        return self.data[key]


class FileCodeCache:
    """A JSON file, `{term_key: {code, matched_text, match_score, ...,
    adjudicated}}`, shared by the api, the eval and the notebooks. Persisted
    between runs, so a fresh clone codes the gold chains without the
    container, and a human can set `adjudicated: true` after review. The
    file is rewritten whole on every new term; a few hundred entries make
    that cheap."""

    def __init__(self, client: IcdClient, path: Path = CACHE_PATH):
        self.client = client
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8") or "{}")

    def code(self, text: str) -> dict:
        key = cache_key(text)
        if key not in self.data:
            self.data[key] = self.client.autocode(text)
            self._save()
        return self.data[key]

    def _save(self) -> None:
        # The api container, the eval and the notebooks all write this file;
        # merge what another writer added since we loaded it, then rewrite.
        if self.path.exists():
            on_disk = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.data = {**on_disk, **self.data}
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:  # LF on Windows too
            json.dump(self.data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)  # a crash mid-write leaves the old file intact


# --------------------------------------------------------------------------
# code_draft
# --------------------------------------------------------------------------


def icd_certificate(cert: dict, cache: CodeCache, demographics: dict | None) -> IcdCertificate:
    """Map a model-produced MCCD onto the API's certificate shape."""
    p1 = [c.get("certified_term", "") for c in (cert.get("part1") or [])]
    p2 = [c.get("certified_term", "") for c in (cert.get("part2") or [])]
    intervals = []
    for c in cert.get("part1") or []:
        iv = c.get("interval") or {}
        intervals.append(iso_duration(iv.get("value"), iv.get("unit")))
    demo = demographics or {}
    return IcdCertificate(
        sex=SEX.get(str(demo.get("sex") or "").lower()),
        estimated_age=demo.get("age_iso"),
        part1_codes=[cache.code(t)["code"] if t else None for t in p1],
        part1_intervals=intervals,
        part2_codes=[cache.code(t)["code"] for t in p2 if t],
    )


def code_draft(
    cert: dict, cache: CodeCache, client: IcdClient, demographics: dict | None
) -> CodedDraft:
    c = icd_certificate(cert, cache, demographics)
    p1_terms = [x.get("certified_term", "") for x in (cert.get("part1") or [])]
    p2_terms = [x.get("certified_term", "") for x in (cert.get("part2") or [])]
    entries = [cache.code(t) for t in p1_terms + p2_terms if t]
    doris = client.doris(c)
    codedit = client.codedit(c)
    ucod = doris.get("ucod_stem_code")
    codedit_ucod = (
        client.codedit(IcdCertificate(sex=c.sex, estimated_age=c.estimated_age, part1_codes=[ucod]))
        if ucod
        else None
    )
    return CodedDraft(
        codes={"part1": c.part1_codes or [], "part2": list(c.part2_codes or [])},
        uncoded_part1=[i for i, code in enumerate(c.part1_codes or []) if not code],
        code_scores=[cache.code(t)["match_score"] if t else None for t in p1_terms],
        autocode=entries,
        doris=doris,
        codedit=codedit,
        codedit_ucod=codedit_ucod,
    )
