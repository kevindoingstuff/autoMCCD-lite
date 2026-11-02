"""The api: POST /certify, GET /healthz, GET /readyz.

One record per request, certified in this request. The pipeline returns the
Result; save writes the JSON file and then the row. A ModelUnavailable is
raised before anything is saved, and the caller gets a 503 to retry.

Start it with `automccd-lite serve` (cli.py), which also starts the WHO
icd-api container.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from automccd_lite import __version__
from automccd_lite.config import settings
from automccd_lite.decide import as_response
from automccd_lite.draft import ModelUnavailable
from automccd_lite.log import log_event
from automccd_lite.models import Record
from automccd_lite.pipeline import Deps, build_deps, certify_record
from automccd_lite.store import create_all, save, session_scope, store_dir

log = logging.getLogger("automccd_lite.api")


class Readiness(BaseModel):
    ready: bool
    components: dict[str, Any]


def readiness(deps: Deps) -> dict[str, bool | str]:
    """The three things a request needs: the WHO container, the store, a model key."""
    components: dict[str, bool | str] = {}
    try:
        components["icd_api"] = deps.who.available()
    except Exception as e:  # noqa: BLE001 - reported, not raised
        components["icd_api"] = type(e).__name__
    try:
        with session_scope() as s:
            s.execute(text("select 1"))
        components["store"] = store_dir().is_dir()
    except Exception as e:  # noqa: BLE001
        components["store"] = type(e).__name__
    components["model_configured"] = bool(settings().env.openai_api_key)
    return components


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    app.state.deps = build_deps()
    yield


app = FastAPI(title="autoMCCD-lite", version=__version__, lifespan=lifespan)


@app.exception_handler(ModelUnavailable)
async def _model_unavailable(request: Request, exc: ModelUnavailable) -> Response:
    log_event(log, "model_unavailable", logging.WARNING, error=type(exc).__name__, http_status=503)
    return JSONResponse(status_code=503, content={"detail": "model unavailable; retry later"})


@app.post("/certify")
def certify(record: Record, request: Request) -> dict:
    result = certify_record(record, request.app.state.deps)
    save(result)
    log_event(
        log,
        "certified",
        record_id=result.record_id,
        route=result.route.value,
        reasons=result.reasons,
        draft_status=result.draft_status,
        timings_ms=result.timings_ms,
        usage=result.usage.model_dump(),
        model=result.versions.model,
        prompt_sha1=result.versions.prompt_sha1,
    )
    return as_response(result)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/readyz", response_model=Readiness)
def readyz(request: Request, response: Response) -> Readiness:
    components = readiness(request.app.state.deps)
    ready = all(v is True for v in components.values())
    response.status_code = 200 if ready else 503
    return Readiness(ready=ready, components=components)
