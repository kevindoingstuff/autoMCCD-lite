"""Blob storage for certificate JSON.

Blob name inside the container: {yyyy-mm-dd}/{hhmmss}Z_{record_id}.json. The
time prefix means a re-run of the same record never collides with the first.
Azurite locally through AZURE_STORAGE_CONNECTION_STRING; the same adapter
works against Azure Blob Storage with a real connection string.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings

from automccd_lite.certificate import as_response
from automccd_lite.config import Settings, settings
from automccd_lite.models import Certificate


def blob_name(record_id: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{now:%Y-%m-%d}/{now:%H%M%S}Z_{record_id}.json"


class BlobStorage:
    def __init__(self, cfg: Settings | None = None):
        cfg = cfg or settings()
        env = cfg.env
        if not env.azure_storage_connection_string:
            raise RuntimeError("set AZURE_STORAGE_CONNECTION_STRING")
        service = BlobServiceClient.from_connection_string(env.azure_storage_connection_string)
        self.container = service.get_container_client(env.azure_storage_container)
        try:
            self.container.create_container()
        except ResourceExistsError:
            pass

    def put_certificate(self, cert: Certificate) -> str:
        name = blob_name(cert.record_id)
        body = json.dumps(as_response(cert), ensure_ascii=False, indent=2).encode("utf-8")
        self.container.upload_blob(
            name, body, overwrite=True, content_settings=ContentSettings("application/json")
        )
        return f"{self.container.container_name}/{name}"

    def exists(self, path: str) -> bool:
        name = path.split("/", 1)[1] if "/" in path else path
        return self.container.get_blob_client(name).exists()
