"""Where results are saved: one folder, `store/` (STORE_DIR in .env moves it).

    store/certificates.sqlite                                 table `certificates`
    store/certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json  the response body

save writes the JSON file first, then the row that records its path, so a
row never points at a missing file. The file is what a downstream system
picks up; the row keeps everything the response leaves out (the rule check,
the fixes, the raw WHO replies) for a reviewer or an audit.

The table is created at startup with create_all; there are no migrations.
SQLAlchemy's JSON type keeps the row shape the same as a Postgres JSONB
table would have.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from automccd_lite.config import settings
from automccd_lite.decide import as_response
from automccd_lite.models import Result

DB_NAME = "certificates.sqlite"
CERTIFICATES_DIR = "certificates"


def store_dir() -> Path:
    return settings().env.store_dir


def db_path() -> Path:
    return store_dir() / DB_NAME


def certificate_path(record_id: str, now: datetime | None = None) -> str:
    """The file name, relative to the store folder. The time prefix means a
    re-run of the same record never collides with the first."""
    now = now or datetime.now(UTC)
    return f"{CERTIFICATES_DIR}/{now:%Y-%m-%d}/{now:%H%M%S}Z_{record_id}.json"


def stored_file(rel: str) -> Path:
    """A saved file's full path, from the relative path in its row."""
    return store_dir() / rel


# ---------------------------------------------------------------------------
# the table


class Base(DeclarativeBase):
    pass


class CertificateRow(Base):
    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    route: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    draft_status: Mapped[str] = mapped_column(String(16), nullable=False)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    certificate: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the MCCD; patient text
    coding: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the summary in the response
    rule_check: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # rules, fixes; patient text
    who_replies: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # autocode, DORIS, CoDEdit
    icd_error: Mapped[str | None] = mapped_column(String(32))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timings_ms: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(256))  # relative to the store folder


@lru_cache(maxsize=1)
def engine() -> Engine:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path.as_posix()}", future=True)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    s = _session_factory()()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def create_all() -> None:
    Base.metadata.create_all(engine())


# ---------------------------------------------------------------------------
# save and read back


def _dump(model: Any) -> dict[str, Any] | None:
    return model.model_dump(mode="json") if model is not None else None


def save(result: Result) -> str:
    """Write the response body as a JSON file, then the row that records its
    path. Returns the file path, relative to the store folder."""
    rel = certificate_path(result.record_id)
    path = stored_file(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(as_response(result), f, ensure_ascii=False, indent=2)
        f.write("\n")
    with session_scope() as s:
        s.add(
            CertificateRow(
                record_id=result.record_id,
                route=result.route.value,
                reasons=list(result.reasons),
                draft_status=result.draft_status,
                versions=_dump(result.versions),
                certificate=result.certificate,
                coding=_dump(result.coding),
                rule_check=_dump(result.rule_check),
                who_replies=_dump(result.who_replies),
                icd_error=result.icd_error,
                usage=_dump(result.usage),
                timings_ms=result.timings_ms,
                file_path=rel,
            )
        )
    return rel


def latest_row(record_id: str) -> CertificateRow | None:
    """The newest saved row for a record."""
    with session_scope() as s:
        return s.scalars(
            select(CertificateRow)
            .where(CertificateRow.record_id == record_id)
            .order_by(CertificateRow.id.desc())
        ).first()
