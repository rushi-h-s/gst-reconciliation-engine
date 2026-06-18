#!/usr/bin/env python3
"""End-to-end smoke test for the GST Reconciliation Engine.

Pre-conditions:
  1. docker-compose stack is running  (docker-compose up api worker pgmq_db)
  2. Supabase schema migration has been applied
  3. Ollama is reachable with the configured model loaded
  4. Copy .env.example → .env and fill in your real values

Run:
  python scripts/smoke_test.py

Optional flags:
  --api-url  URL of the running API  (default: http://localhost:8000)
  --setup-only   Create org+client, print IDs, then exit (no upload)
  --cleanup      Delete the smoke-test org+client at the end

The test uses a real GSTIN (27ABCDE1234F1Z5, verified checksum) embedded
in a synthetic invoice image so the VLM has something coherent to read.
"""

import argparse
import io
import json
import os
import sys
import time
import uuid
from datetime import date

import httpx

# ── Constants for the synthetic invoice ──────────────────────────────────────
# 27ABCDE1234F1Z5: state 27 (MH), PAN ABCDE1234F, entity 1, checksum 5 (verified).
TEST_GSTIN    = "27ABCDE1234F1Z5"
TEST_INV_NO   = "INV2026001"
TEST_DATE     = "2026-06-01"
TEST_TV       = "10000.00"
TEST_CGST     = "900.00"
TEST_SGST     = "900.00"
TEST_IGST     = "0.00"
TEST_PERIOD   = "2026-06"


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ── Synthetic invoice image ───────────────────────────────────────────────────

def make_invoice_png() -> bytes:
    """Create a simple invoice PNG using Pillow.

    If Pillow is not installed the test falls back to a 1x1 white pixel PNG
    and warns that VLM extraction will likely fail (but the pipeline still runs).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        img = Image.new("RGB", (600, 500), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Try to load a basic font; fall back to default if not available.
        try:
            font = ImageFont.truetype("arial.ttf", 18)
            font_sm = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            font = ImageFont.load_default()
            font_sm = font

        lines = [
            ("TAX INVOICE", (50, 30), font),
            (f"Supplier GSTIN : {TEST_GSTIN}", (50, 80), font_sm),
            ("Supplier Name  : ABCDE Enterprises Pvt Ltd", (50, 110), font_sm),
            (f"Invoice No     : {TEST_INV_NO}", (50, 140), font_sm),
            (f"Invoice Date   : {TEST_DATE}", (50, 170), font_sm),
            ("", (50, 200), font_sm),
            (f"Taxable Value  : INR {TEST_TV}", (50, 230), font_sm),
            (f"CGST @9%       : INR {TEST_CGST}", (50, 260), font_sm),
            (f"SGST @9%       : INR {TEST_SGST}", (50, 290), font_sm),
            ("IGST           : INR 0.00", (50, 320), font_sm),
            ("Total Invoice  : INR 11800.00", (50, 360), font_sm),
            ("is_rcm: false   doc_type: invoice   confidence: 0.95", (50, 420), font_sm),
        ]
        for text, pos, fnt in lines:
            draw.text(pos, text, fill=(0, 0, 0), font=fnt)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except ImportError:
        log("WARNING: Pillow not installed — using 1x1 placeholder PNG.")
        log("         VLM extraction will likely fail. Install: pip install pillow")
        # Minimal valid PNG (1x1 white pixel)
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
            b"\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8"
            b"\xff\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND"
            b"\xaeB`\x82"
        )


# ── Supabase setup helpers ────────────────────────────────────────────────────

def create_org_and_client(supabase_url: str, service_key: str) -> tuple[str, str]:
    """Insert a smoke-test org + client into Supabase, return (org_id, client_id)."""
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    # Create org
    org_id = str(uuid.uuid4())
    resp = httpx.post(
        f"{supabase_url}/rest/v1/orgs",
        headers=headers,
        json={"id": org_id, "name": "Smoke Test Org"},
    )
    if resp.status_code not in (200, 201):
        die(f"Could not create org: {resp.status_code} {resp.text}")
    log(f"Created org: {org_id}")

    # Create client
    client_id = str(uuid.uuid4())
    resp = httpx.post(
        f"{supabase_url}/rest/v1/clients",
        headers=headers,
        json={"id": client_id, "org_id": org_id, "name": "Smoke Test Client", "gstin": TEST_GSTIN},
    )
    if resp.status_code not in (200, 201):
        die(f"Could not create client: {resp.status_code} {resp.text}")
    log(f"Created client: {client_id}")

    return org_id, client_id


def delete_org(supabase_url: str, service_key: str, org_id: str) -> None:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }
    resp = httpx.delete(
        f"{supabase_url}/rest/v1/orgs",
        headers=headers,
        params={"id": f"eq.{org_id}"},
    )
    if resp.status_code in (200, 204):
        log(f"Cleaned up org {org_id} (cascade deletes client + all entries)")
    else:
        log(f"WARNING: cleanup failed: {resp.status_code} {resp.text}")


# ── API helpers ───────────────────────────────────────────────────────────────

def api_headers(org_id: str) -> dict:
    return {"x-org-id": org_id}


def check_health(api_url: str) -> None:
    log("Checking /health …")
    try:
        r = httpx.get(f"{api_url}/health", timeout=10)
        data = r.json()
    except Exception as exc:
        die(f"API not reachable at {api_url}: {exc}")

    log(f"  status   : {data.get('status')}")
    log(f"  ollama   : {data.get('ollama')}")
    log(f"  queue    : {data.get('queue')}")
    log(f"  supabase : {data.get('supabase')}")
    log(f"  model    : {data.get('model')}")

    if not data.get("queue"):
        die("Queue (pgmq) is not reachable — start docker-compose first.")
    if not data.get("supabase"):
        die("Supabase is not reachable — check SUPABASE_URL and SUPABASE_SERVICE_KEY.")
    if not data.get("ollama"):
        log("WARNING: Ollama not reachable — extraction will fail. Continuing anyway.")


def upload_invoice(api_url: str, org_id: str, client_id: str, image: bytes) -> str:
    log("Uploading invoice image …")
    r = httpx.post(
        f"{api_url}/api/v1/invoices/upload",
        headers=api_headers(org_id),
        params={"client_id": client_id, "period": TEST_PERIOD},
        files={"file": ("smoke_invoice.png", image, "image/png")},
        timeout=30,
    )
    if r.status_code != 202:
        die(f"Invoice upload failed: {r.status_code} {r.text}")
    data = r.json()
    extraction_id = data["extraction_id"]
    log(f"  extraction_id : {extraction_id}  status: {data['status']}")
    return extraction_id


def poll_extraction(api_url: str, org_id: str, extraction_id: str, timeout: int = 180) -> dict:
    log(f"Polling extraction status (timeout={timeout}s) …")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(
            f"{api_url}/api/v1/invoices/{extraction_id}",
            headers=api_headers(org_id),
            timeout=10,
        )
        if r.status_code != 200:
            die(f"Status poll failed: {r.status_code} {r.text}")
        data = r.json()
        status = data["status"]
        log(f"  {status} …")
        if status == "extracted":
            log(f"  confidence: {data.get('confidence')}")
            return data
        if status == "failed":
            errors = data.get("validation_errors", [])
            die(
                f"Extraction failed.\n"
                f"  errors: {errors}\n\n"
                "Common causes:\n"
                "  - VLM returned markdown fences around JSON (see ollama.py:50)\n"
                "  - GSTIN in image failed checksum (see invoice.py:66)\n"
                "  - Decimal with commas (see _to_decimal)\n"
                "  - Date not in YYYY-MM-DD format\n"
                "  - Ollama model not loaded — run: ollama pull qwen2.5vl:7b"
            )
        time.sleep(3)
    die(f"Extraction timed out after {timeout}s — is the worker running?")


def build_gstr2b_json() -> dict:
    """Build a minimal GSTR-2B JSON matching the synthetic invoice."""
    return {
        "data": {
            "docdata": {
                "b2b": [
                    {
                        "ctin": TEST_GSTIN,
                        "trdnm": "ABCDE Enterprises Pvt Ltd",
                        "inv": [
                            {
                                "inum": TEST_INV_NO,
                                "dt": TEST_DATE,          # ISO format; government files use DD-MM-YYYY
                                "txval": float(TEST_TV),
                                "camt": float(TEST_CGST),
                                "samt": float(TEST_SGST),
                                "iamt": float(TEST_IGST),
                            }
                        ],
                    }
                ]
            }
        }
    }


def upload_gstr2b(api_url: str, org_id: str, client_id: str) -> int:
    log("Uploading GSTR-2B JSON …")
    payload = json.dumps(build_gstr2b_json()).encode()
    r = httpx.post(
        f"{api_url}/api/v1/gstr2b/upload",
        headers=api_headers(org_id),
        params={"client_id": client_id, "period": TEST_PERIOD},
        files={"file": ("2b.json", payload, "application/json")},
        timeout=30,
    )
    if r.status_code != 201:
        die(f"GSTR-2B upload failed: {r.status_code} {r.text}")
    data = r.json()
    inserted = data.get("inserted", 0)
    log(f"  inserted {inserted} entries")
    if inserted == 0:
        log("  WARNING: 0 entries inserted — check the GSTR-2B JSON structure.")
    return inserted


def run_reconciliation(api_url: str, org_id: str, client_id: str) -> list:
    log("Running reconciliation …")
    r = httpx.post(
        f"{api_url}/api/v1/reconciliation/run",
        headers=api_headers(org_id),
        params={"client_id": client_id, "period": TEST_PERIOD},
        timeout=60,
    )
    if r.status_code != 201:
        die(f"Reconciliation failed: {r.status_code} {r.text}")
    data = r.json()
    return data.get("results", [])


def print_results(results: list) -> None:
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    log("")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("  RECONCILIATION RESULTS")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for status, count in sorted(counts.items()):
        log(f"  {status:<12} : {count}")
    log(f"  {'TOTAL':<12} : {len(results)}")
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("")

    for r in results:
        entry = r.get("pr_entry") or r.get("gstr2b_entry") or {}
        log(
            f"  [{r['status']:<10}] "
            f"inv={entry.get('inv_no', '—')}  "
            f"gstin={entry.get('supplier_gstin', '—')}  "
            f"conf={r.get('confidence', '—')}  "
            f"mismatch={r.get('mismatched_fields') or '[]'}"
        )

    # Happy-path verdict
    log("")
    if counts.get("MATCHED", 0) == 1 and len(results) == 1:
        log("PASS: Happy path confirmed — 1 MATCHED result.")
    else:
        log(f"INFO: Got {len(results)} result(s). "
            "Check statuses above; MATCHED=1 is the happy-path target.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="GST Engine smoke test")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--setup-only", action="store_true",
                        help="Create org+client and print IDs, then exit")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete smoke-test org (cascade) after the run")
    args = parser.parse_args()

    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key  = os.environ.get("SUPABASE_SERVICE_KEY", "")
    api_url      = args.api_url.rstrip("/")

    if not supabase_url or not service_key:
        die("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment.")

    log(f"API: {api_url}")
    log(f"Supabase: {supabase_url}")
    log("")

    # 1. Health check
    check_health(api_url)

    # 2. Create isolated org + client
    org_id, client_id = create_org_and_client(supabase_url, service_key)

    if args.setup_only:
        log("")
        log("Setup complete. Add these to your .env:")
        log(f"  VITE_ORG_ID={org_id}")
        log(f"  # client_id: {client_id}")
        return

    try:
        # 3. Upload invoice image
        image = make_invoice_png()
        extraction_id = upload_invoice(api_url, org_id, client_id, image)

        # 4. Poll until worker finishes extraction
        poll_extraction(api_url, org_id, extraction_id, timeout=180)

        # 5. Upload GSTR-2B with matching data
        upload_gstr2b(api_url, org_id, client_id)

        # 6. Run reconciliation
        results = run_reconciliation(api_url, org_id, client_id)

        # 7. Print outcome
        print_results(results)

    finally:
        if args.cleanup:
            delete_org(supabase_url, service_key, org_id)
        else:
            log(f"Org + client left in Supabase for inspection.")
            log(f"  org_id    : {org_id}")
            log(f"  client_id : {client_id}")
            log("Re-run with --cleanup to delete them.")


if __name__ == "__main__":
    main()
