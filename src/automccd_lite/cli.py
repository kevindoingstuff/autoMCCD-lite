"""The `automccd-lite` command: everything an apprentice runs from a terminal.

    automccd-lite serve             start the WHO icd-api container, create the store, run the api
    automccd-lite icd start|stop|status   the container alone (the eval and the notebooks need it)
    automccd-lite smoke             the end-to-end check against a running `serve`
    automccd-lite eval [...]        the mini harness (python -m automccd_lite.eval)

The WHO ships the ICD-API only as a Docker image, so Docker is the one
prerequisite beside uv and a model key. The container is named, pinned to a
digest and left running between serves, because it takes about a minute to
load its release each time it starts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

from automccd_lite.config import DATA_DIR, ROOT, settings
from automccd_lite.log import configure_logging

ICD_CONTAINER = "automccd-lite-icd"
# config/default.yaml code.icd_release must match the release this image serves (2026-01).
ICD_IMAGE = "whoicd/icd-api@sha256:1b77eb6dc43e0c65a12e9e9340ad178493c93488e728d0d936507cc57ada1b7c"
ICD_WAIT_S = 240


def say(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# .env


def ensure_env() -> None:
    """Create .env from the example when it is missing, and stop until the
    model key is filled in. Everything else has a working default."""
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        fail("created .env from .env.example; fill OPENAI_BASE_URL and OPENAI_API_KEY, run again")
    if not settings().env.openai_api_key:
        fail("OPENAI_API_KEY is empty in .env; fill it and run again")


# ---------------------------------------------------------------------------
# the icd-api container


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", *args]  # the docker CLI from PATH, with fixed arguments
    return subprocess.run(cmd, capture_output=True, text=True, check=check)  # noqa: S603, S607


def _icd_port() -> str:
    return os.environ.get("ICD_PORT", "8082")


def icd_state() -> str | None:
    """'running', 'exited', ... or None when the container does not exist."""
    if shutil.which("docker") is None:
        fail("docker is not installed; the WHO icd-api runs only as a container")
    r = _docker("inspect", "-f", "{{.State.Status}}", ICD_CONTAINER, check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def icd_start() -> None:
    state = icd_state()
    if state == "running":
        say(f"icd-api: container {ICD_CONTAINER} is running")
    elif state is None:
        say(f"icd-api: starting {ICD_CONTAINER} on port {_icd_port()} (pulls the image once)")
        r = _docker(
            "run", "-d", "--name", ICD_CONTAINER, "-p", f"{_icd_port()}:80",
            "-e", "acceptLicense=true", "-e", "enableDoris=true", ICD_IMAGE, check=False,
        )  # fmt: skip
        if r.returncode != 0:
            fail(f"docker run failed: {r.stderr.strip()[:300]}")
    else:
        say(f"icd-api: container {ICD_CONTAINER} is {state}; starting it")
        _docker("start", ICD_CONTAINER)
    icd_wait()


def icd_wait() -> None:
    """The container answers only after it has loaded the release (about a
    minute, longer on the first start when it downloads it)."""
    from automccd_lite.api import icd_client

    icd = icd_client()
    t0 = time.monotonic()
    while time.monotonic() - t0 < ICD_WAIT_S:
        if icd.available():
            say(f"icd-api: ready at {icd.base_url} after {time.monotonic() - t0:.0f} s")
            return
        time.sleep(3)
    fail(f"icd-api did not become ready within {ICD_WAIT_S} s; see `docker logs {ICD_CONTAINER}`")


def icd_stop() -> None:
    if icd_state() is None:
        say(f"icd-api: no container named {ICD_CONTAINER}")
        return
    _docker("rm", "-f", ICD_CONTAINER)
    say(f"icd-api: removed {ICD_CONTAINER}")


def icd_status() -> None:
    state = icd_state()
    say(f"icd-api: container {ICD_CONTAINER} {state or 'absent'}")
    if state == "running":
        from automccd_lite.api import icd_client

        say(f"icd-api: release reachable: {icd_client().available()}")


# ---------------------------------------------------------------------------
# serve and smoke


def serve() -> None:
    ensure_env()
    icd_start()
    cfg = settings()
    cfg.env.store_dir.mkdir(parents=True, exist_ok=True)
    say(f"store: {cfg.env.store_dir}")
    configure_logging()
    import uvicorn

    uvicorn.run(
        "automccd_lite.api:app",
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=cfg.env.api_port,
        log_config=None,
    )


def smoke() -> None:
    """readyz -> POST /certify with data/records/S01.json -> the row -> the file."""
    from sqlalchemy import select

    from automccd_lite.db import CertificateRow, session_scope
    from automccd_lite.storage import FileStorage

    cfg = settings()
    api = os.environ.get("API_URL", f"http://localhost:{cfg.env.api_port}")
    say(f"1. waiting for {api}/readyz")
    ready = None
    for _ in range(90):
        try:
            ready = httpx.get(f"{api}/readyz", timeout=10)
            if ready.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    if ready is None or ready.status_code != 200:
        fail(f"not ready after 90 s: {ready.text if ready is not None else 'no answer'}")
    say(f"   ready: {ready.text}")

    say("2. POST /certify (S01)")
    body = json.loads((DATA_DIR / "records" / "S01.json").read_text(encoding="utf-8"))
    r = httpx.post(f"{api}/certify", json=body, timeout=300)
    if r.status_code != 200:
        fail(f"/certify returned {r.status_code}: {r.text[:300]}")
    resp = r.json()
    if resp.get("route") not in ("auto", "review"):
        fail(f"bad route {resp.get('route')!r}")
    if "prompt_sha1" not in (resp.get("versions") or {}):
        fail("no versions block")
    say(f"   route={resp['route']} reasons={resp['reasons']}")

    say("3. the stored row and its file")
    with session_scope() as s:
        row = s.scalars(
            select(CertificateRow)
            .where(CertificateRow.record_id == "S01")
            .order_by(CertificateRow.id.desc())
        ).first()
    if row is None:
        fail("no certificates row for S01")
    if not row.file_path or not FileStorage(cfg.env.store_dir).exists(row.file_path):
        fail(f"row {row.id} has no file: {row.file_path}")
    say(f"   row {row.id}: {row.route} {row.file_path}")
    say("SMOKE OK")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(prog="automccd-lite", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="start the icd-api container, create the store, run the api")
    icd = sub.add_parser("icd", help="the WHO icd-api container alone")
    icd.add_argument("action", choices=["start", "stop", "status"])
    sub.add_parser("smoke", help="the end-to-end check against a running serve")
    ev = sub.add_parser("eval", help="the mini harness; arguments go to automccd_lite.eval")
    ev.add_argument("args", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)

    if a.cmd == "serve":
        serve()
    elif a.cmd == "icd":
        {"start": icd_start, "stop": icd_stop, "status": icd_status}[a.action]()
    elif a.cmd == "smoke":
        smoke()
    elif a.cmd == "eval":
        from automccd_lite.eval import main as eval_main

        eval_main(a.args)


if __name__ == "__main__":
    main()
