"""Storage of the original files (Azure Blob Storage).

The container is private; read access only happens through temporary SAS
URLs. Services depend on the `BlobStorage` protocol, which allows
replacing the implementation in tests.
"""

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import BinaryIO, Protocol
from urllib.parse import quote

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)

from core.config import StorageSettings, settings
from core.exceptions import NotFoundError, ServiceUnavailableError

logger = logging.getLogger(__name__)


class BlobStorage(Protocol):
    def upload(self, key: str, data: BinaryIO, content_type: str, *, as_attachment: bool = False) -> str:
        """Envia o arquivo e retorna o nome completo do blob (a ser persistido)."""

    def download(self, blob_name: str) -> bytes: ...

    def delete(self, blob_name: str) -> None: ...

    def generate_read_url(self, blob_name: str, expires_at: datetime) -> str: ...


class AzureBlobStorage:
    def __init__(self, config: StorageSettings) -> None:
        self._account_name = config.stg_account_name
        self._account_key = config.stg_account_key.get_secret_value()
        self._container_name = config.container_name
        self._prefix = config.blob_prefix
        self._account_url = f"https://{self._account_name}.blob.core.windows.net"

        service = BlobServiceClient(account_url=self._account_url, credential=self._account_key)
        self._container = service.get_container_client(self._container_name)

    def _blob_name(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def upload(self, key: str, data: BinaryIO, content_type: str, *, as_attachment: bool = False) -> str:
        blob_name = self._blob_name(key)
        content_settings = ContentSettings(
            content_type=content_type,
            content_disposition="attachment" if as_attachment else None,
        )

        try:
            self._container.upload_blob(
                name=blob_name,
                data=data,
                overwrite=False,
                content_settings=content_settings,
                max_concurrency=1,  # low memory usage (small VPS)
            )
        except AzureError as exc:
            logger.exception("Blob upload failed | blob=%s", blob_name)
            raise ServiceUnavailableError("File storage unavailable") from exc

        return blob_name

    def download(self, blob_name: str) -> bytes:
        try:
            return self._container.download_blob(blob_name, max_concurrency=1).readall()
        except ResourceNotFoundError as exc:
            raise NotFoundError("Blob not found") from exc
        except AzureError as exc:
            logger.exception("Blob download failed | blob=%s", blob_name)
            raise ServiceUnavailableError("File storage unavailable") from exc

    def delete(self, blob_name: str) -> None:
        try:
            self._container.delete_blob(blob_name)
        except ResourceNotFoundError:
            pass
        except AzureError as exc:
            logger.exception("Blob delete failed | blob=%s", blob_name)
            raise ServiceUnavailableError("File storage unavailable") from exc

    def generate_read_url(self, blob_name: str, expires_at: datetime) -> str:
        sas = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container_name,
            blob_name=blob_name,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            start=datetime.now(UTC) - timedelta(minutes=5),  # clock skew tolerance
            expiry=expires_at,
            protocol="https",
        )
        return f"{self._account_url}/{self._container_name}/{quote(blob_name)}?{sas}"


@lru_cache
def get_blob_storage() -> BlobStorage:
    """FastAPI dependency: one client per process (reuses HTTP connections)."""
    return AzureBlobStorage(settings.storage)
