"""Core reconciliation matching engine.

Compares purchase_register_entries against gstr2b_entries and assigns
one of 5 match statuses to every invoice on both sides.
"""

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

# ±₹1 tolerance covers common rounding differences across tax splits.
_AMOUNT_TOLERANCE = Decimal("1.00")
# Minimum inv_no similarity to call a fuzzy match PROBABLE.
_FUZZY_THRESHOLD = 0.60


@dataclass
class _Entry:
    id: str
    norm_supplier_gstin: str
    norm_inv_no: str
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal


def _to_entry(row: dict) -> _Entry:
    return _Entry(
        id=row["id"],
        norm_supplier_gstin=row.get("norm_supplier_gstin") or "",
        norm_inv_no=row.get("norm_inv_no") or "",
        taxable_value=Decimal(str(row.get("taxable_value") or 0)),
        cgst=Decimal(str(row.get("cgst") or 0)),
        sgst=Decimal(str(row.get("sgst") or 0)),
        igst=Decimal(str(row.get("igst") or 0)),
    )


def _mismatched_amounts(pr: _Entry, b2b: _Entry) -> list[str]:
    mismatches = []
    for name, pv, bv in [
        ("taxable_value", pr.taxable_value, b2b.taxable_value),
        ("cgst", pr.cgst, b2b.cgst),
        ("sgst", pr.sgst, b2b.sgst),
        ("igst", pr.igst, b2b.igst),
    ]:
        if abs(pv - bv) > _AMOUNT_TOLERANCE:
            mismatches.append(name)
    return mismatches


def run_matching(pr_rows: list[dict], b2b_rows: list[dict]) -> list[dict]:
    """Return list of match-result dicts ready for insertion into match_results.

    Each dict has: pr_entry_id, gstr2b_entry_id, status, confidence,
    mismatched_fields.
    """
    pr_entries = [_to_entry(r) for r in pr_rows]
    b2b_entries = [_to_entry(r) for r in b2b_rows]

    # Exact-key index: (norm_gstin, norm_inv_no) → first 2B entry found.
    b2b_by_key: dict[tuple[str, str], _Entry] = {}
    for e in b2b_entries:
        b2b_by_key.setdefault((e.norm_supplier_gstin, e.norm_inv_no), e)

    # GSTIN-only index for fuzzy pass.
    b2b_by_gstin: dict[str, list[_Entry]] = {}
    for e in b2b_entries:
        b2b_by_gstin.setdefault(e.norm_supplier_gstin, []).append(e)

    matched_b2b_ids: set[str] = set()
    results: list[dict] = []

    for pr in pr_entries:
        exact = b2b_by_key.get((pr.norm_supplier_gstin, pr.norm_inv_no))

        if exact:
            matched_b2b_ids.add(exact.id)
            mismatches = _mismatched_amounts(pr, exact)
            if not mismatches:
                results.append({
                    "pr_entry_id": pr.id,
                    "gstr2b_entry_id": exact.id,
                    "status": "MATCHED",
                    "confidence": 1.0,
                    "mismatched_fields": [],
                })
            else:
                confidence = round(max(0.40, 1.0 - len(mismatches) * 0.15), 3)
                results.append({
                    "pr_entry_id": pr.id,
                    "gstr2b_entry_id": exact.id,
                    "status": "MISMATCH",
                    "confidence": confidence,
                    "mismatched_fields": mismatches,
                })
            continue

        # Fuzzy pass: same GSTIN, amounts within tolerance, best inv_no match.
        best: _Entry | None = None
        best_score = 0.0
        for candidate in b2b_by_gstin.get(pr.norm_supplier_gstin, []):
            if candidate.id in matched_b2b_ids:
                continue
            if _mismatched_amounts(pr, candidate):
                continue
            score = SequenceMatcher(None, pr.norm_inv_no, candidate.norm_inv_no).ratio()
            if score > best_score:
                best_score = score
                best = candidate

        if best and best_score >= _FUZZY_THRESHOLD:
            matched_b2b_ids.add(best.id)
            confidence = round(min(0.95, 0.50 + best_score * 0.45), 3)
            results.append({
                "pr_entry_id": pr.id,
                "gstr2b_entry_id": best.id,
                "status": "PROBABLE",
                "confidence": confidence,
                "mismatched_fields": ["inv_no"],
            })
        else:
            results.append({
                "pr_entry_id": pr.id,
                "gstr2b_entry_id": None,
                "status": "BOOKS_ONLY",
                "confidence": 1.0,
                "mismatched_fields": [],
            })

    # Any 2B entry not consumed by a PR match → TWOB_ONLY.
    for b2b in b2b_entries:
        if b2b.id not in matched_b2b_ids:
            results.append({
                "pr_entry_id": None,
                "gstr2b_entry_id": b2b.id,
                "status": "TWOB_ONLY",
                "confidence": 1.0,
                "mismatched_fields": [],
            })

    return results
