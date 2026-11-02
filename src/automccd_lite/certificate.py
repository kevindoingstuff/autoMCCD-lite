"""generate_certificate: the Result, and where it goes.

Builds the response object from everything the stages produced. When a
storage and a store are passed it also writes the JSON file and inserts the
certificates row; the eval passes neither and only builds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from automccd_lite import __version__
from automccd_lite.config import settings
from automccd_lite.models import (
    Coding,
    CodingSummary,
    Decision,
    Draft,
    DraftStatus,
    Record,
    Result,
    RuleCheck,
    Usage,
    Versions,
)
from automccd_lite.prompt import system_prompt

if TYPE_CHECKING:
    from automccd_lite.db import SqliteCertificateStore
    from automccd_lite.storage import FileStorage


def coding_summary(coded: Coding | None) -> CodingSummary | None:
    if coded is None:
        return None
    issues = list(coded.codedit.get("issue_ids") or [])
    if coded.codedit_cause:
        issues += [i for i in coded.codedit_cause.get("issue_ids") or [] if i not in issues]
    return CodingSummary(
        underlying_cause=coded.doris.get("ucod_stem_code"),
        underlying_cause_full=coded.doris.get("ucod_code"),
        codes=coded.codes,
        match_scores=coded.match_scores,
        quality_issues=issues,
        cause_warning=coded.doris.get("warning"),
    )


def versions(model: str | None) -> Versions:
    sp = system_prompt()
    cfg = settings()
    return Versions(
        prompt_version=sp.version,
        prompt_sha1=sp.sha1,
        schema_version=sp.schema_version,
        model=model or cfg.draft.model,
        icd_release=cfg.code.icd_release,
        package_version=__version__,
    )


def generate_certificate(
    record: Record,
    draft: Draft,
    validation: RuleCheck | None,
    coded: Coding | None,
    icd_error: str | None,
    decision: Decision,
    usage: Usage,
    timings_ms: dict[str, int],
    storage: FileStorage | None = None,
    store: SqliteCertificateStore | None = None,
) -> Result:
    status: DraftStatus = draft.status
    cert = Result(
        record_id=record.record_id,
        route=decision.route,
        reasons=decision.reasons,
        certificate=draft.certificate,
        coding=coding_summary(coded),
        versions=versions(draft.model),
        usage=usage,
        timings_ms=timings_ms,
        draft_status=status,
        validation=validation,
        coded=coded,
        icd_error=icd_error,
    )
    file_path: str | None = None
    if storage is not None:
        file_path = storage.put_certificate(cert)  # the file first; the row records its path
    if store is not None:
        store.insert_certificate(cert, file_path)
    return cert


def as_response(cert: Result) -> dict[str, Any]:
    """The API body: the certificate without the bulky provenance, which
    stays in the database."""
    return cert.model_dump(
        mode="json", exclude={"validation", "coded", "icd_error", "draft_status"}
    )
