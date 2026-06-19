import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import AsyncClient

from app.dependencies import get_org_id, get_supabase
from app.reconciliation import run_matching
from app.schemas.invoice import CorrectionRequest

router = APIRouter()

_PR_FIELDS = (
    "id, supplier_gstin, supplier_name, inv_no, inv_date, "
    "taxable_value, cgst, sgst, igst, is_rcm, doc_type"
)
_B2B_FIELDS = (
    "id, supplier_gstin, supplier_name, inv_no, inv_date, "
    "taxable_value, cgst, sgst, igst, doc_type"
)


@router.get("")
async def get_results(
    client_id: str = Query(...),
    period: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    org_id: str = Depends(get_org_id),
    supabase: AsyncClient = Depends(get_supabase),
):
    """Return paginated match results, auto-running the matcher if none exist yet."""
    has_results = (
        await supabase.table("match_results")
        .select("id")
        .eq("org_id", org_id)
        .eq("client_id", client_id)
        .eq("period", period)
        .limit(1)
        .execute()
    )
    if not has_results.data:
        await _run_and_persist(org_id, client_id, period, supabase)

    results, total_count = await _fetch_with_entries(
        org_id, client_id, period, supabase, page, page_size
    )
    return {
        "results": results,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total_count / page_size)),
    }


@router.post("/run", status_code=201)
async def run_reconciliation(
    client_id: str = Query(...),
    period: str = Query(...),
    org_id: str = Depends(get_org_id),
    supabase: AsyncClient = Depends(get_supabase),
):
    """Force re-run the matcher, replacing any previous results for this period."""
    await supabase.table("match_results").delete().eq("org_id", org_id).eq(
        "client_id", client_id
    ).eq("period", period).execute()
    await _run_and_persist(org_id, client_id, period, supabase)
    results, total_count = await _fetch_with_entries(
        org_id, client_id, period, supabase, page=1, page_size=50
    )
    return {"count": total_count, "results": results}


@router.post("/{result_id}/correct")
async def correct_result(
    result_id: str,
    body: CorrectionRequest,
    org_id: str = Depends(get_org_id),
    supabase: AsyncClient = Depends(get_supabase),
):
    """Apply a human correction to a PROBABLE or MISMATCH row.

    Sets status to CORRECTED and stores the override values for audit.
    """
    row = (
        await supabase.table("match_results")
        .select("id")
        .eq("id", result_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Result not found")

    await supabase.table("match_results").update(
        {
            "status": "CORRECTED",
            "corrected_amount": body.corrected_amount,
            "corrected_date": body.corrected_date.isoformat() if body.corrected_date else None,
            "correction_reason": body.reason,
            "correction_notes": body.notes,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", result_id).execute()

    updated = (
        await supabase.table("match_results")
        .select("*")
        .eq("id", result_id)
        .single()
        .execute()
    )
    return updated.data


@router.patch("/{result_id}/confirm")
async def confirm_result(
    result_id: str,
    org_id: str = Depends(get_org_id),
    supabase: AsyncClient = Depends(get_supabase),
):
    """Mark a PROBABLE/MISMATCH row as human-reviewed (maker-checker confirm)."""
    row = (
        await supabase.table("match_results")
        .select("id")
        .eq("id", result_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Result not found")
    await supabase.table("match_results").update(
        {"reviewed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", result_id).execute()
    return {"ok": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_and_persist(
    org_id: str, client_id: str, period: str, supabase: AsyncClient
) -> None:
    pr_rows = (
        await supabase.table("purchase_register_entries")
        .select("*")
        .eq("org_id", org_id)
        .eq("client_id", client_id)
        .eq("period", period)
        .execute()
    ).data or []

    b2b_rows = (
        await supabase.table("gstr2b_entries")
        .select("*")
        .eq("org_id", org_id)
        .eq("client_id", client_id)
        .eq("period", period)
        .execute()
    ).data or []

    if not pr_rows and not b2b_rows:
        return

    matches = run_matching(pr_rows, b2b_rows)
    rows = [
        {
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "client_id": client_id,
            "period": period,
            **m,
        }
        for m in matches
    ]
    if rows:
        await supabase.table("match_results").insert(rows).execute()


async def _fetch_with_entries(
    org_id: str,
    client_id: str,
    period: str,
    supabase: AsyncClient,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    offset = (page - 1) * page_size
    resp = (
        await supabase.table("match_results")
        .select("*", count="exact")
        .eq("org_id", org_id)
        .eq("client_id", client_id)
        .eq("period", period)
        .order("created_at")
        .range(offset, offset + page_size - 1)
        .execute()
    )
    results = resp.data or []
    total_count: int = resp.count or 0

    pr_ids = [r["pr_entry_id"] for r in results if r.get("pr_entry_id")]
    b2b_ids = [r["gstr2b_entry_id"] for r in results if r.get("gstr2b_entry_id")]

    pr_map: dict[str, dict] = {}
    b2b_map: dict[str, dict] = {}

    if pr_ids:
        rows = (
            await supabase.table("purchase_register_entries")
            .select(_PR_FIELDS)
            .in_("id", pr_ids)
            .execute()
        ).data or []
        pr_map = {r["id"]: r for r in rows}

    if b2b_ids:
        rows = (
            await supabase.table("gstr2b_entries")
            .select(_B2B_FIELDS)
            .in_("id", b2b_ids)
            .execute()
        ).data or []
        b2b_map = {r["id"]: r for r in rows}

    hydrated = [
        {
            **r,
            "pr_entry": pr_map.get(r["pr_entry_id"]) if r.get("pr_entry_id") else None,
            "gstr2b_entry": (
                b2b_map.get(r["gstr2b_entry_id"]) if r.get("gstr2b_entry_id") else None
            ),
        }
        for r in results
    ]
    return hydrated, total_count
