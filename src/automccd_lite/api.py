"""The api: POST /certify, GET /healthz, GET /readyz.

One record per request, run in this request, in one transaction: the Blob is
written first, then the row commits; a ModelUnavailable rolls everything
back, so nothing is stored and the caller gets a 503 to retry.

    python -m automccd_lite.api      (or: uvicorn automccd_lite.api:app)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from automccd_lite import __version__
from automccd_lite.certificate import as_response
from automccd_lite.code import FileCodeCache, IcdClient
from automccd_lite.config import Settings, settings
from automccd_lite.db import PostgresCertificateStore, create_all, session_scope
from automccd_lite.draft import Ask, ModelUnavailable, asker, make_client
from automccd_lite.log import configure_logging, log_event
from automccd_lite.models import Certificate, Record
from automccd_lite.pipeline import Deps, certify_record
from automccd_lite.storage import BlobStorage

log = logging.getLogger("automccd_lite.api")


class Readiness(BaseModel):
    ready: bool
    components: dict[str, Any]


@dataclass
class Runtime:
    """The collaborators built once at startup."""

    settings: Settings
    ask: Ask
    icd: IcdClient
    cache: FileCodeCache
    storage: BlobStorage

    @classmethod
    def from_env(cls) -> Runtime:
        cfg = settings()
        icd = IcdClient(
            cfg.env.icd_api_url,
            cfg.code.icd_release,
            cfg.code.linearization,
            cfg.code.timeout_s,
            cfg.code.max_retries,
        )
        return cls(
            settings=cfg,
            ask=asker(make_client(cfg)),
            icd=icd,
            cache=FileCodeCache(icd),
            storage=BlobStorage(cfg),
        )

    def certify_one(self, record: Record) -> Certificate:
        with session_scope() as s:
            deps = Deps(self.ask, self.icd, self.cache, self.storage, PostgresCertificateStore(s))
            cert = certify_record(record, deps)
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
            with session_scope() as s:
                s.execute(text("select 1"))
            components["postgres"] = True
        except Exception as e:  # noqa: BLE001 - reported, not raised
            components["postgres"] = type(e).__name__
        try:
            components["icd_api"] = self.icd.available()
        except Exception as e:  # noqa: BLE001
            components["icd_api"] = type(e).__name__
        try:
            components["storage"] = self.storage.container.exists()
        except Exception as e:  # noqa: BLE001
            components["storage"] = type(e).__name__
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


def main() -> None:
    configure_logging()
    uvicorn.run(
        "automccd_lite.api:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),  # noqa: S104 - a container port
        port=int(os.environ.get("API_PORT", "8000")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
