"""Background worker — polls pgmq and runs VLM extraction.

Run with:  python worker.py
Or via Docker Compose (see docker-compose.yml).
"""

import asyncio
import logging
from app.config import settings
from app.db import get_supabase_client
from app.extraction.ollama import OllamaExtractionProvider
from app.normalization import normalize_gstin, normalize_invoice_number
from app.queue.pgmq import PgmqClient
from app.schemas.invoice import ExtractionJob
from app.storage.client import SupabaseStorageProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("worker")


async def process(msg: dict, provider: OllamaExtractionProvider, supabase) -> None:
    job = ExtractionJob(**msg["message"])
    log.info("extraction_id=%s status=starting", job.extraction_id)

    storage = SupabaseStorageProvider(supabase)

    try:
        image_bytes = await storage.download(job.storage_path)
        extracted = await provider.extract(image_bytes)

        norm_gstin = normalize_gstin(extracted.supplier_gstin)
        norm_inv_no = normalize_invoice_number(extracted.inv_no)

        # Persist the raw VLM output.
        await supabase.table("extractions").update(
            {
                "raw_vlm_json": extracted.model_dump(mode="json"),
                "status": "extracted",
                "confidence": float(extracted.confidence),
                "validation_errors": [],
            }
        ).eq("id", job.extraction_id).execute()

        # Write the structured purchase register entry.
        if job.client_id and job.period:
            await supabase.table("purchase_register_entries").insert(
                {
                    "org_id": job.org_id,
                    "client_id": job.client_id,
                    "period": job.period,
                    "supplier_gstin": extracted.supplier_gstin,
                    "supplier_name": extracted.supplier_name,
                    "inv_no": extracted.inv_no,
                    "inv_date": extracted.inv_date.isoformat(),
                    "taxable_value": str(extracted.taxable_value),
                    "cgst": str(extracted.cgst),
                    "sgst": str(extracted.sgst),
                    "igst": str(extracted.igst),
                    "is_rcm": extracted.is_rcm,
                    "doc_type": extracted.doc_type,
                    "source_image_hash": job.image_hash,
                    "extraction_id": job.extraction_id,
                    "norm_supplier_gstin": norm_gstin,
                    "norm_inv_no": norm_inv_no,
                }
            ).execute()

        log.info("extraction_id=%s status=done confidence=%.2f", job.extraction_id, extracted.confidence)

    except Exception as exc:
        log.exception("extraction_id=%s status=failed", job.extraction_id)
        await supabase.table("extractions").update(
            {
                "status": "failed",
                "validation_errors": [str(exc)],
            }
        ).eq("id", job.extraction_id).execute()


async def main() -> None:
    queue = PgmqClient()
    await queue.connect()

    provider = OllamaExtractionProvider()
    supabase = await get_supabase_client()

    log.info("Worker online — queue=%s model=%s", settings.queue_name, settings.ollama_model)

    while True:
        messages = await queue.read(
            vt=settings.worker_visibility_timeout_seconds,
            batch_size=1,
        )
        if not messages:
            await asyncio.sleep(settings.worker_poll_interval_seconds)
            continue

        for msg in messages:
            await process(msg, provider, supabase)
            await queue.archive(msg["msg_id"])


if __name__ == "__main__":
    asyncio.run(main())
