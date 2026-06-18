import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings
from app.db import get_supabase_client
from app.extraction.ollama import OllamaExtractionProvider
from app.middleware import JWTAuthMiddleware
from app.queue.pgmq import PgmqClient
from app.routers import gstr2b, invoices, reconciliation

log = logging.getLogger("gst_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await get_supabase_client()  # warm up singleton

    queue = PgmqClient()
    try:
        await queue.connect()
        log.info("Queue connected (pgmq)")
    except Exception as exc:
        log.warning("Queue unavailable — invoice uploads will fail until pgmq is enabled: %s", exc)
    app.state.queue = queue

    provider = OllamaExtractionProvider()
    if not await provider.health_check():
        log.warning(
            "Ollama not reachable at %s — extraction will fail until it starts",
            settings.ollama_base_url,
        )
    app.state.extraction_provider = provider

    yield

    # --- shutdown ---
    await app.state.queue.close()


app = FastAPI(
    title="GST Reconciliation Engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(JWTAuthMiddleware)

app.include_router(invoices.router, prefix="/api/v1/invoices", tags=["invoices"])
app.include_router(gstr2b.router, prefix="/api/v1/gstr2b", tags=["gstr2b"])
app.include_router(reconciliation.router, prefix="/api/v1/reconciliation", tags=["reconciliation"])


@app.get("/health", tags=["ops"])
async def health():
    """Check all three dependencies independently.

    Returns 200 even when a dependency is down — the caller inspects each
    boolean field to determine which component is failing.
    """
    provider: OllamaExtractionProvider = app.state.extraction_provider
    queue: PgmqClient = app.state.queue
    supabase = await get_supabase_client()

    # Run all three checks concurrently.
    import asyncio
    ollama_ok, queue_ok, supabase_ok = await asyncio.gather(
        provider.health_check(),
        queue.health_check(),
        _supabase_health(supabase),
        return_exceptions=False,
    )

    all_ok = ollama_ok and queue_ok and supabase_ok
    return {
        "status": "ok" if all_ok else "degraded",
        "ollama": ollama_ok,
        "queue": queue_ok,
        "supabase": supabase_ok,
        "model": settings.ollama_model,
        "queue_name": settings.queue_name,
    }


async def _supabase_health(supabase) -> bool:
    try:
        await supabase.table("orgs").select("id").limit(1).execute()
        return True
    except Exception:
        return False
