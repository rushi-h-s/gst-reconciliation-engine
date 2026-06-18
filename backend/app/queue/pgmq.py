import json
from typing import Any, Optional
import asyncpg
from app.config import settings


class PgmqClient:
    """Thin async wrapper around the pgmq Postgres extension.

    pgmq lives entirely in Postgres — send/read/delete are plain SQL calls.
    Swap queue backends by replacing this class; the interface is the three
    methods below.
    """

    def __init__(self, dsn: str | None = None, queue_name: str | None = None) -> None:
        self._dsn = dsn or settings.supabase_db_url
        self._queue = queue_name or settings.queue_name
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError(
                "Queue not connected. Enable the pgmq extension in Supabase "
                "(Dashboard → Database → Extensions → pgmq) then restart."
            )
        return self._pool

    async def send(self, message: dict[str, Any]) -> int:
        """Enqueue a message; return the msg_id."""
        async with self._require_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT pgmq.send($1, $2::jsonb)",
                self._queue,
                json.dumps(message),
            )
            return row[0]

    async def read(self, vt: int | None = None, batch_size: int = 1) -> list[dict]:
        """Dequeue up to *batch_size* messages, invisible for *vt* seconds."""
        vt = vt or settings.worker_visibility_timeout_seconds
        async with self._require_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM pgmq.read($1, $2, $3)",
                self._queue,
                vt,
                batch_size,
            )
            return [dict(r) for r in rows]

    async def archive(self, msg_id: int) -> None:
        """Move a processed message to the archive table (keeps audit trail)."""
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                "SELECT pgmq.archive($1, $2)", self._queue, msg_id
            )

    async def delete(self, msg_id: int) -> None:
        """Permanently delete a message (no audit trail)."""
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                "SELECT pgmq.delete($1, $2)", self._queue, msg_id
            )

    async def health_check(self) -> bool:
        """Return True when the queue DB is reachable and pgmq extension is loaded."""
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT pgmq.list_queues()")
            return True
        except Exception:
            return False
