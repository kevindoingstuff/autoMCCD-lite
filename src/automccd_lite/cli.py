"""The `automccd-lite` command: everything an apprentice runs from a terminal.

    automccd-lite serve                 start the icd-api container, create the store, run the api
    automccd-lite icd start|stop|status the container alone (the eval and the notebooks need it)
    automccd-lite example [S01]         certify one example record through the running api
    automccd-lite eval --set provided   the mini harness; --set dlc for the 40 harder records
    automccd-lite notebooks             execute both notebooks against the running api
    automccd-lite docs                  build the Sphinx site into docs/_build

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
# config/default.yaml check.icd_release must match the release this image serves (2026-01).
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
    from automccd_lite.pipeline import who_client

    icd = who_client()
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
        from automccd_lite.pipeline import who_client

        say(f"icd-api: release reachable: {who_client().available()}")


# ---------------------------------------------------------------------------
# serve


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


def wait_ready(api: str, seconds: int = 90) -> str:
    """The /readyz body once the api answers ready; a clear error otherwise.
    No answer at all for 5 s means no api; an answer of not ready (the
    container still loading its release) is waited out."""
    last = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        try:
            last = httpx.get(f"{api}/readyz", timeout=10)
            if last.status_code == 200:
                return last.text
        except httpx.HTTPError:
            if last is None and time.monotonic() - t0 > 5:
                break
        time.sleep(1)
    if last is None:
        fail(f"no api at {api}; start it with `uv run automccd-lite serve` in another terminal")
    fail(f"the api is not ready after {seconds} s: {last.text}")
    raise AssertionError("unreachable")


def print_certificate(resp: dict) -> None:
    """The response as the WHO form: Part I with intervals and codes, Part II,
    the underlying cause."""
    cert = resp.get("certificate") or {}
    coding = resp.get("coding") or {}
    codes = coding.get("codes") or {}

    def line(label: str, c: dict, code: str | None) -> str:
        iv = c.get("interval") or {}
        when = f"{iv.get('value')} {iv.get('unit')}" if iv.get("value") is not None else "unknown"
        return f"  {label:<4}{c.get('certified_term', ''):<46}{when:<16}{code or '-'}"

    say("")
    say(f"Record {resp['record_id']}    route: {resp['route']}")
    say(f"Reasons: {', '.join(resp['reasons']) or 'none (every check passed)'}")
    if cert.get("assessment") != "certifiable":
        reason = cert.get("decline_reason") or "-"
        say(f"Declined: {cert.get('assessment')} ({reason})")
        return
    say("")
    say(f"  {'':<4}{'Part I: the chain, immediate cause first':<46}{'interval':<16}ICD-11")
    for i, c in enumerate(cert.get("part1") or []):
        say(line("1" + "abcde"[i], c, (codes.get("part1") or [None] * 9)[i]))
    part2 = cert.get("part2") or []
    if part2:
        say(f"  {'':<4}Part II: other significant conditions")
        for i, c in enumerate(part2):
            p2 = codes.get("part2") or []
            say(line("", c, p2[i] if i < len(p2) else None))
    say("")
    say(f"Underlying cause (UCOD): {coding.get('underlying_cause') or '-'}")


def example(record_id: str) -> None:
    """POST one example record to the running api, print the result as the
    WHO form, and confirm the row and the file were saved."""
    from automccd_lite.store import db_path, latest_row, stored_file

    path = DATA_DIR / "examples" / f"{record_id}.json"
    if not path.exists():
        names = ", ".join(p.stem for p in sorted(path.parent.glob("*.json")))
        fail(f"no example {record_id}; one of {names}")
    cfg = settings()
    api = os.environ.get("API_URL", f"http://localhost:{cfg.env.api_port}")
    say(f"Waiting for {api}/readyz ...")
    say(f"  {wait_ready(api)}")
    say(f"POST /certify with {path.relative_to(ROOT).as_posix()} (one model call, a few seconds)")
    body = json.loads(path.read_text(encoding="utf-8"))
    r = httpx.post(f"{api}/certify", json=body, timeout=300)
    if r.status_code != 200:
        fail(f"/certify returned {r.status_code}: {r.text[:300]}")
    resp = r.json()
    print_certificate(resp)

    row = latest_row(body["record_id"])
    if row is None or not row.file_path or not stored_file(row.file_path).is_file():
        fail(f"the result for {body['record_id']} was not saved")
    store = cfg.env.store_dir
    say("")
    say(f"Saved: row {row.id} in {db_path().relative_to(store.parent).as_posix()}")
    say(f"       {(store / row.file_path).relative_to(store.parent).as_posix()}")
    say("EXAMPLE OK")


def run_module(group: str, *args: str) -> None:
    """Run a tool from one of the optional dependency groups."""
    import importlib.util

    if importlib.util.find_spec(args[0]) is None:
        fail(f"{args[0]} is not installed; run `uv sync --group {group}` first")
    raise SystemExit(subprocess.call([sys.executable, "-m", *args], cwd=ROOT))  # noqa: S603


def notebooks() -> None:
    run_module(
        "notebooks",
        "jupyter", "nbconvert", "--execute", "--inplace",
        "--ExecutePreprocessor.timeout=1800", "--ExecutePreprocessor.kernel_name=python3",
        "notebooks/how-to-guide.ipynb", "notebooks/mccd_guide.ipynb",
    )  # fmt: skip


def docs() -> None:
    run_module("docs", "sphinx", "-b", "html", "docs", "docs/_build")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(prog="automccd-lite", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="start the icd-api container, create the store, run the api")
    icd = sub.add_parser("icd", help="the WHO icd-api container alone")
    icd.add_argument("action", choices=["start", "stop", "status"])
    ex = sub.add_parser("example", help="certify one example record through the running api")
    ex.add_argument("record_id", nargs="?", default="S01", help="S01, S02 or S05")
    sub.add_parser("eval", help="the mini harness: --set provided|dlc, --only <ids>")
    sub.add_parser("notebooks", help="execute both notebooks against the running api")
    sub.add_parser("docs", help="build the Sphinx site into docs/_build")
    a, rest = p.parse_known_args(argv)
    if a.cmd != "eval" and rest:
        p.error(f"unrecognized arguments: {' '.join(rest)}")

    if a.cmd == "serve":
        serve()
    elif a.cmd == "icd":
        {"start": icd_start, "stop": icd_stop, "status": icd_status}[a.action]()
    elif a.cmd == "example":
        example(a.record_id)
    elif a.cmd == "eval":
        from automccd_lite.eval import main as eval_main

        eval_main(rest)
    elif a.cmd == "notebooks":
        notebooks()
    elif a.cmd == "docs":
        docs()


if __name__ == "__main__":
    main()
