import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from supabase import AsyncClient
from app.dependencies import get_org_id, get_queue, get_supabase
from app.normalization import normalize_gstin, normalize_invoice_number
from app.queue.pgmq import PgmqClient
from app.schemas.invoice import ExtractionJob
from app.storage.client import SupabaseStorageProvider, compute_content_hash

router = APIRouter()

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/tiff", "application/pdf"}


def _file_ext(filename: str | None) -> str:
    if filename and "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ".jpg"


@router.post("/upload", status_code=202)
async def upload_invoice(
    file: UploadFile = File(...),
    client_id: str | None = None,
    period: str | None = None,
    org_id: str = Depends(get_org_id),
    queue: PgmqClient = Depends(get_queue),
    supabase: AsyncClient = Depends(get_supabase),
):
    """Accept an invoice image and enqueue it for async VLM extraction.

    Returns immediately with an extraction_id the caller can poll.
    Duplicate images (same SHA-256) are skipped if already extracted.
    """
    if file.content_type not in _ALLOWED_MIME:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {file.content_type}")

    data = await file.read()
    content_hash = compute_content_hash(data)

    # Content-hash dedup: skip re-extraction if this image was already processed.
    existing = (
        await supabase.table("extractions")
        .select("id, status")
        .eq("org_id", org_id)
        .eq("image_hash", content_hash)
        .maybe_single()
        .execute()
    )
    if existing.data and existing.data["status"] == "extracted":
        return {"extraction_id": existing.data["id"], "status": "already_extracted"}

    storage = SupabaseStorageProvider(supabase)
    storage_path = f"{org_id}/{content_hash}{_file_ext(file.filename)}"
    await storage.upload(storage_path, data, file.content_type or "image/jpeg")

    extraction_id = str(uuid.uuid4())
    await supabase.table("extractions").insert(
        {
            "id": extraction_id,
            "org_id": org_id,
            "image_hash": content_hash,
            "storage_path": storage_path,
            "status": "pending",
        }
    ).execute()

    job = ExtractionJob(
        org_id=org_id,
        client_id=client_id or "",
        period=period or "",
        storage_path=storage_path,
        image_hash=content_hash,
        extraction_id=extraction_id,
    )
    await queue.send(job.model_dump())

    return {"extraction_id": extraction_id, "status": "queued"}


@router.get("/{extraction_id}")
async def get_extraction_status(
    extraction_id: str,
    org_id: str = Depends(get_org_id),
    supabase: AsyncClient = Depends(get_supabase),
):
    result = (
        await supabase.table("extractions")
        .select("id, status, confidence, validation_errors, created_at, updated_at")
        .eq("id", extraction_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return result.data
