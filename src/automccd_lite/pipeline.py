"""certify_record: one record through the three steps.

    Draft   draft_certificate
    Check   check_rules -> fix_rules (once, when a rule fails)
            fix_terms (once, when a term codes weakly) -> code_terms
    Decide  decide

The collaborators come in through Deps, so the pipeline runs with a fake
model and a fake coder in the tests and with the real ones in the api and
the eval. It writes nothing; the api saves the Result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from automccd_lite.coding import (
    CodeCache,
    WhoClient,
    WhoError,
    WhoUnavailable,
    code_terms,
    fix_terms,
)
from automccd_lite.config import settings
from automccd_lite.decide import decide
from automccd_lite.draft import Ask, draft_certificate
from automccd_lite.models import Coding, Record, Result, RuleCheck
from automccd_lite.rules import check_rules, fix_rules, needs_fix


@dataclass
class Deps:
    """What the pipeline talks to: the model, the WHO container, the cache."""

    ask: Ask
    who: WhoClient
    cache: CodeCache


class Timer:
    def __init__(self) -> None:
        self.t = time.perf_counter()
        self.ms: dict[str, int] = {}

    def mark(self, step: str) -> None:
        now = time.perf_counter()
        self.ms[step] = self.ms.get(step, 0) + int((now - self.t) * 1000)
        self.t = now


def certify_record(record: Record, deps: Deps) -> Result:
    cfg = settings()
    t = Timer()

    # Step 1. Draft
    draft = draft_certificate(record, deps.ask)
    t.mark("draft")

    # Step 2. Check
    check: RuleCheck | None = None
    coding: Coding | None = None
    icd_error: str | None = None
    if draft.status in ("ok", "unparsable"):
        if draft.certificate is not None:
            check = check_rules(draft.certificate, record.fields)
        if needs_fix(draft, check) and cfg.revise.max_attempts > 0:
            draft, check = fix_rules(record, draft, check, deps.ask)
        if check is not None and check.assessment == "certifiable":
            try:
                if cfg.revise.term_max_attempts > 0:
                    draft, check = fix_terms(
                        record, draft, check, deps.cache, deps.ask, cfg.code.min_match_score
                    )
                coding = code_terms(
                    draft.certificate or {}, deps.cache, deps.who, record.demographics
                )
            except WhoUnavailable:
                icd_error = "who:unavailable"
            except WhoError:
                icd_error = "who:error"
    t.mark("check")

    # Step 3. Decide
    result = decide(record, draft, check, coding, icd_error, t.ms)
    t.mark("decide")
    result.timings_ms = dict(t.ms)
    return result
