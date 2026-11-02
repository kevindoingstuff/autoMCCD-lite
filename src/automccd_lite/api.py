"""The api: POST /certify, GET /healthz, GET /readyz.

One record per request, run in this request, in one transaction: the JSON
file is written first, then the row commits; a ModelUnavailable rolls the
row back, so nothing is stored and the caller gets a 503 to retry.

Start it with `automccd-lite serve` (cli.py), which also starts the WHO
icd-api container; `uvicorn automccd_lite.api:app` works once that runs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from automccd_lite import __version__
from automccd_lite.coding import CodeCache, WhoClient
from automccd_lite.config import Settings, settings
from automccd_lite.db import SqliteCertificateStore, create_all, session_scope
from automccd_lite.decide import as_response
from automccd_lite.draft import Ask, ModelUnavailable, connect_model
from automccd_lite.log import log_event
from automccd_lite.models import Record, Result
from automccd_lite.pipeline import Deps, certify_record
from automccd_lite.storage import FileStorage

log = logging.getLogger("automccd_lite.api")


class Readiness(BaseModel):
    ready: bool
    components: dict[str, Any]


def icd_client(cfg: Settings | None = None) -> WhoClient:
    cfg = cfg or settings()
    return WhoClient(
        cfg.env.icd_api_url,
        cfg.code.icd_release,
        cfg.code.linearization,
        cfg.code.timeout_s,
        cfg.code.max_retries,
    )


@dataclass
class Runtime:
    """The collaborators built once at startup."""

    settings: Settings
    ask: Ask
    icd: WhoClient
    cache: CodeCache
    storage: FileStorage

    @classmethod
    def from_env(cls) -> Runtime:
        cfg = settings()
        icd = icd_client(cfg)
        return cls(
            settings=cfg,
            ask=connect_model(cfg),
            icd=icd,
            cache=CodeCache(icd),
            storage=FileStorage(cfg.env.store_dir),
        )

    def certify_one(self, record: Record) -> Result:
        with session_scope() as s:
            cert = certify_record(record, Deps(self.ask, self.icd, self.cache))
            path = self.storage.put_certificate(cert)  # the file first; the row records its path
            SqliteCertificateStore(s).insert_certificate(cert, path)
        log_event(
            log,
            "certified",
            record_id=cert.record_id,
            route=cert.route.value,
            reasons=cert.reasons,
            draft_status=cert.draft_status,
            timings_ms=cert.timings_ms,
            usage=cert.usage.model_dump(),
            model=cert.versions.model,
            prompt_sha1=cert.versions.prompt_sha1,
        )
        return cert

    def readiness(self) -> dict:
        components: dict[str, bool | str] = {}
        try:
            components["icd_api"] = self.icd.available()
        except Exception as e:  # noqa: BLE001 - reported, not raised
            components["icd_api"] = type(e).__name__
        try:
            with session_scope() as s:
                s.execute(text("select 1"))
            components["store"] = self.storage.store_dir.is_dir()
        except Exception as e:  # noqa: BLE001
            components["store"] = type(e).__name__
        components["model_configured"] = bool(self.settings.env.openai_api_key)
        return components


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    app.state.rt = Runtime.from_env()
    yield


app = FastAPI(title="autoMCCD-lite", version=__version__, lifespan=lifespan)


@app.exception_handler(ModelUnavailable)
async def _model_unavailable(request: Request, exc: ModelUnavailable) -> Response:
    log_event(log, "model_unavailable", logging.WARNING, error=type(exc).__name__, http_status=503)
    return JSONResponse(status_code=503, content={"detail": "model unavailable; retry later"})


@app.post("/certify")
def certify(record: Record, request: Request) -> dict:
    return as_response(request.app.state.rt.certify_one(record))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/readyz", response_model=Readiness)
def readyz(request: Request, response: Response) -> Readiness:
    components = request.app.state.rt.readiness()
    ready = all(v is True for v in components.values())
    response.status_code = 200 if ready else 503
    return Readiness(ready=ready, components=components)
