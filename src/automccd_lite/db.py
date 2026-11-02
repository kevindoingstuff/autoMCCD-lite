"""The certificates table: one provenance row per certified record, in a
SQLite file under the store folder (`STORE_DIR/certificates.sqlite`).

The table is created at startup with `create_all`; there are no migrations.
SQLAlchemy's JSON type keeps the row shape the same as a Postgres JSONB
table would, so the same model works against Postgres later.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, Integer, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from automccd_lite.config import settings
from automccd_lite.models import Result

DB_NAME = "certificates.sqlite"


def db_path() -> Path:
    return settings().env.store_dir / DB_NAME


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
    certificate: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # the MCCD; PHI
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # rules, revisions; PHI
    coding: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # autocode, DORIS, CoDEdit
    icd_error: Mapped[str | None] = mapped_column(String(32))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timings_ms: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(256))  # the JSON copy, under STORE_DIR


@lru_cache(maxsize=1)
def engine() -> Engine:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path.as_posix()}", future=True)


@lru_cache(maxsize=1)
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    s = session_factory()()
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


class SqliteCertificateStore:
    def __init__(self, session: Session):
        self.session = session

    def insert_certificate(self, cert: Result, file_path: str | None) -> None:
        self.session.add(
            CertificateRow(
                record_id=cert.record_id,
                route=cert.route.value,
                reasons=list(cert.reasons),
                draft_status=cert.draft_status,
                versions=cert.versions.model_dump(mode="json"),
                certificate=cert.certificate,
                validation=cert.validation.model_dump(mode="json") if cert.validation else None,
                coding=cert.coded.model_dump(mode="json") if cert.coded else None,
                icd_error=cert.icd_error,
                usage=cert.usage.model_dump(mode="json"),
                timings_ms=cert.timings_ms,
                file_path=file_path,
            )
        )
