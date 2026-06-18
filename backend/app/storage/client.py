import hashlib
from abc import ABC, abstractmethod
from supabase import AsyncClient


def compute_content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StorageProvider(ABC):
    """Interface for raw file storage.

    Swap implementations by replacing the registered provider.
    """

    @abstractmethod
    async def upload(self, path: str, data: bytes, content_type: str) -> str:
        """Store *data* at *path*; return the storage path."""
        ...

    @abstractmethod
    async def download(self, path: str) -> bytes:
        ...


class SupabaseStorageProvider(StorageProvider):
    BUCKET = "invoice-images"

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def upload(
        self, path: str, data: bytes, content_type: str = "image/jpeg"
    ) -> str:
        await self._client.storage.from_(self.BUCKET).upload(
            path,
            data,
            {"content-type": content_type, "upsert": "false"},
        )
        return path

    async def download(self, path: str) -> bytes:
        return await self._client.storage.from_(self.BUCKET).download(path)
