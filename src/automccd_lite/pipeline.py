"""certify_record: one record through every stage.

build_prompt -> draft_certificate -> validate_draft -> revise_draft
-> code_draft (with revise_terms inside) -> route_draft -> generate_certificate

Collaborators are passed in, so the pipeline runs with a fake model and a
fake coder in unit tests and with the real ones in the api and the eval.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from automccd_lite.certificate import CertificateStore, Storage, generate_certificate
from automccd_lite.code import CodeCache, IcdClient, IcdError, ICDUnavailable, code_draft
from automccd_lite.config import settings
from automccd_lite.draft import Ask, draft_certificate
from automccd_lite.models import Certificate, CodedDraft, DraftValidation, Record
from automccd_lite.revise import needs_revision, revise_draft, revise_terms
from automccd_lite.route import route_draft
from automccd_lite.validate import validate_draft


@dataclass
class Deps:
    ask: Ask
    icd: IcdClient
    cache: CodeCache
    storage: Storage | None = None
    store: CertificateStore | None = None


class Timer:
    def __init__(self) -> None:
        self.t = time.perf_counter()
        self.ms: dict[str, int] = {}

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        self.ms[stage] = self.ms.get(stage, 0) + int((now - self.t) * 1000)
        self.t = now


def certify_record(record: Record, deps: Deps) -> Certificate:
    cfg = settings()
    t = Timer()

    draft = draft_certificate(record, deps.ask)
    t.mark("draft")

    validation: DraftValidation | None = None
    coded: CodedDraft | None = None
    icd_error: str | None = None

    if draft.status in ("ok", "invalid"):
        if draft.certificate is not None:
            validation = validate_draft(draft.certificate, record.fields)
        t.mark("validate")

        if needs_revision(draft, validation) and cfg.revise.max_attempts > 0:
            draft, validation = revise_draft(record, draft, validation, deps.ask)
            t.mark("revise")

        if validation is not None and validation.assessment == "certifiable":
            try:
                if cfg.revise.term_max_attempts > 0:  # terminology revision, inside coding
                    draft, validation = revise_terms(
                        record, draft, validation, deps.cache, deps.ask, cfg.code.min_match_score
                    )
                    t.mark("revise")
                coded = code_draft(
                    draft.certificate or {}, deps.cache, deps.icd, record.demographics
                )
            except ICDUnavailable:
                icd_error = "icd_unavailable"
            except IcdError:
                icd_error = "icd_error"
            t.mark("code")

    decision = route_draft(record, draft, validation, coded, icd_error, cfg.code.min_match_score)
    t.mark("route")
    return generate_certificate(
        record, draft, validation, coded, icd_error, decision, draft.usage, t.ms,
        storage=deps.storage, store=deps.store,
    )  # fmt: skip
