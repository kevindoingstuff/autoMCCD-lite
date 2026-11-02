"""The certificate JSON files: one per certified record, under the store folder.

File name: `certificates/{yyyy-mm-dd}/{hhmmss}Z_{record_id}.json` relative to
`STORE_DIR`. The time prefix means a re-run of the same record never
collides with the first. The body is the api response, pretty-printed, so a
downstream system can pick it up without the database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from automccd_lite.config import settings
from automccd_lite.models import Result

CERTIFICATES_DIR = "certificates"


def certificate_path(record_id: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{CERTIFICATES_DIR}/{now:%Y-%m-%d}/{now:%H%M%S}Z_{record_id}.json"


class FileStorage:
    def __init__(self, store_dir: Path | None = None):
        self.store_dir = store_dir or settings().env.store_dir
        (self.store_dir / CERTIFICATES_DIR).mkdir(parents=True, exist_ok=True)

    def put_certificate(self, cert: Result) -> str:
        from automccd_lite.certificate import as_response  # the file holds the response body

        rel = certificate_path(cert.record_id)
        path = self.store_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(as_response(cert), f, ensure_ascii=False, indent=2)
            f.write("\n")
        return rel

    def exists(self, rel: str) -> bool:
        return (self.store_dir / rel).is_file()

    def read(self, rel: str) -> dict:
        return json.loads((self.store_dir / rel).read_text(encoding="utf-8"))
