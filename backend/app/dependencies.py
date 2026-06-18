from fastapi import Request
from supabase import AsyncClient
from app.queue.pgmq import PgmqClient
from app.extraction.provider import ExtractionProvider
from app.db import get_supabase_client


async def get_queue(request: Request) -> PgmqClient:
    return request.app.state.queue


async def get_extraction_provider(request: Request) -> ExtractionProvider:
    return request.app.state.extraction_provider


async def get_org_id(request: Request) -> str:
    """Return the org_id deposited by JWTAuthMiddleware.

    The middleware has already validated the token and guaranteed this
    attribute is set before any route handler runs.
    """
    return request.state.org_id


async def get_supabase() -> AsyncClient:
    return await get_supabase_client()
