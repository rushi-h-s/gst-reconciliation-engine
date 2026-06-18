"""Unit tests for the reconciliation matching engine.

Pure Python — no live services, no DB, no Ollama required.
Run from backend/: pytest
"""

from difflib import SequenceMatcher
from decimal import Decimal
import pytest
from app.reconciliation import run_matching, _AMOUNT_TOLERANCE, _FUZZY_THRESHOLD


# ── Test-data helpers ─────────────────────────────────────────────────────────

GSTIN_A = "27GSTIN00000001"
GSTIN_B = "29GSTIN00000002"


def pr(
    id: str = "pr1",
    gstin: str = GSTIN_A,
    inv: str = "INV001",
    tv: str = "10000",
    cgst: str = "900",
    sgst: str = "900",
    igst: str = "0",
) -> dict:
    return {
        "id": id,
        "norm_supplier_gstin": gstin,
        "norm_inv_no": inv,
        "taxable_value": tv,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
    }


def b2b(
    id: str = "b1",
    gstin: str = GSTIN_A,
    inv: str = "INV001",
    tv: str = "10000",
    cgst: str = "900",
    sgst: str = "900",
    igst: str = "0",
) -> dict:
    return {
        "id": id,
        "norm_supplier_gstin": gstin,
        "norm_inv_no": inv,
        "taxable_value": tv,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
    }


def single(pr_row, b2b_row):
    """Run matcher with one PR and one 2B row, return the first result."""
    results = run_matching([pr_row], [b2b_row])
    assert len(results) >= 1
    return results[0]


# ── MATCHED ───────────────────────────────────────────────────────────────────

class TestMatched:
    def test_exact_key_and_amounts(self):
        r = single(pr(), b2b())
        assert r["status"] == "MATCHED"
        assert r["confidence"] == 1.0
        assert r["mismatched_fields"] == []
        assert r["pr_entry_id"] == "pr1"
        assert r["gstr2b_entry_id"] == "b1"

    def test_tolerance_boundary_exactly_one_rupee_over(self):
        """abs(pv - bv) == ₹1.00 → NOT > tolerance → still MATCHED."""
        tol = str(_AMOUNT_TOLERANCE)        # "1.00"
        r = single(
            pr(tv="10001.00"),
            b2b(tv="10000.00"),
        )
        assert r["status"] == "MATCHED", (
            f"Expected MATCHED when diff == ₹{tol}, got {r['status']}"
        )

    def test_tolerance_boundary_one_rupee_under(self):
        """Subtract ₹1.00 from the other side — should also MATCH."""
        r = single(pr(tv="9999.00"), b2b(tv="10000.00"))
        assert r["status"] == "MATCHED"

    def test_igst_instead_of_cgst_sgst(self):
        """IGST-only invoice (inter-state) matches correctly."""
        r = single(
            pr(cgst="0", sgst="0", igst="1800"),
            b2b(cgst="0", sgst="0", igst="1800"),
        )
        assert r["status"] == "MATCHED"

    def test_zero_tax_invoice(self):
        """Zero-tax invoice (exempt supply) matches."""
        r = single(
            pr(cgst="0", sgst="0", igst="0"),
            b2b(cgst="0", sgst="0", igst="0"),
        )
        assert r["status"] == "MATCHED"


# ── MISMATCH ──────────────────────────────────────────────────────────────────

class TestMismatch:
    def test_taxable_value_over_tolerance(self):
        """₹1.01 difference → MISMATCH with taxable_value flagged."""
        r = single(pr(tv="10001.01"), b2b(tv="10000.00"))
        assert r["status"] == "MISMATCH"
        assert "taxable_value" in r["mismatched_fields"]
        assert r["pr_entry_id"] == "pr1"
        assert r["gstr2b_entry_id"] == "b1"

    def test_one_field_mismatch_confidence(self):
        """1 mismatched field → confidence = 1.0 - 1*0.15 = 0.85."""
        r = single(pr(cgst="999.00"), b2b(cgst="900.00"))
        assert r["status"] == "MISMATCH"
        assert len(r["mismatched_fields"]) == 1
        assert r["confidence"] == pytest.approx(0.85, abs=1e-3)

    def test_two_field_mismatch_confidence(self):
        """2 mismatched fields → confidence = 1.0 - 2*0.15 = 0.70."""
        r = single(
            pr(cgst="999.00", sgst="999.00"),
            b2b(cgst="900.00", sgst="900.00"),
        )
        assert r["status"] == "MISMATCH"
        assert len(r["mismatched_fields"]) == 2
        assert r["confidence"] == pytest.approx(0.70, abs=1e-3)

    def test_three_field_mismatch_confidence(self):
        """3 mismatched fields → confidence = 1.0 - 3*0.15 = 0.55."""
        r = single(
            pr(tv="9000.00", cgst="999.00", sgst="999.00"),
            b2b(tv="10000.00", cgst="900.00", sgst="900.00"),
        )
        assert r["status"] == "MISMATCH"
        assert len(r["mismatched_fields"]) == 3
        assert r["confidence"] == pytest.approx(0.55, abs=1e-3)

    def test_four_field_mismatch_confidence_floor(self):
        """4 mismatched fields → floor at 0.40."""
        r = single(
            pr(tv="9000", cgst="800", sgst="800", igst="500"),
            b2b(tv="10000", cgst="900", sgst="900", igst="0"),
        )
        assert r["status"] == "MISMATCH"
        assert len(r["mismatched_fields"]) == 4
        assert r["confidence"] == pytest.approx(0.40, abs=1e-3)

    def test_mismatch_preserves_both_ids(self):
        """MISMATCH must link both sides so reviewer can compare."""
        r = single(pr(tv="99999"), b2b(tv="1"))
        assert r["pr_entry_id"] == "pr1"
        assert r["gstr2b_entry_id"] == "b1"


# ── PROBABLE ──────────────────────────────────────────────────────────────────

class TestProbable:
    # PR: "INV2024001", 2B: "INV24001"
    # SequenceMatcher("INV2024001", "INV24001") shares "INV" + "24001" = 8 matching chars
    # ratio = 2*8 / (10+8) = 0.889 ≥ 0.60 → PROBABLE
    _PR_INV = "INV2024001"   # 10 chars
    _B2B_INV = "INV24001"    # 8 chars (year digits dropped)

    def test_probable_status(self):
        score = SequenceMatcher(None, self._PR_INV, self._B2B_INV).ratio()
        assert score >= _FUZZY_THRESHOLD, (
            f"Test setup broken: similarity {score:.3f} < threshold {_FUZZY_THRESHOLD}"
        )
        r = single(
            pr(inv=self._PR_INV),
            b2b(inv=self._B2B_INV),
        )
        assert r["status"] == "PROBABLE"
        assert r["mismatched_fields"] == ["inv_no"]
        assert r["pr_entry_id"] == "pr1"
        assert r["gstr2b_entry_id"] == "b1"

    def test_probable_confidence_in_range(self):
        r = single(pr(inv=self._PR_INV), b2b(inv=self._B2B_INV))
        assert 0.60 <= r["confidence"] <= 0.95

    def test_probable_confidence_formula(self):
        """confidence = min(0.95, 0.50 + score * 0.45)."""
        score = SequenceMatcher(None, self._PR_INV, self._B2B_INV).ratio()
        expected = round(min(0.95, 0.50 + score * 0.45), 3)
        r = single(pr(inv=self._PR_INV), b2b(inv=self._B2B_INV))
        assert r["confidence"] == pytest.approx(expected, abs=1e-3)

    def test_probable_requires_amount_match(self):
        """Same GSTIN + similar inv_no but amounts differ → BOOKS_ONLY not PROBABLE."""
        r = single(
            pr(inv=self._PR_INV, tv="99999"),   # amount intentionally different
            b2b(inv=self._B2B_INV, tv="1"),
        )
        # The fuzzy candidate fails the amount check, so no match → BOOKS_ONLY
        assert r["status"] == "BOOKS_ONLY"


# ── BOOKS_ONLY ────────────────────────────────────────────────────────────────

class TestBooksOnly:
    def test_no_2b_entry_at_all(self):
        results = run_matching([pr()], [])
        assert len(results) == 1
        r = results[0]
        assert r["status"] == "BOOKS_ONLY"
        assert r["pr_entry_id"] == "pr1"
        assert r["gstr2b_entry_id"] is None
        assert r["confidence"] == 1.0
        assert r["mismatched_fields"] == []

    def test_different_gstin_no_fuzzy(self):
        """PR and 2B have same inv_no but different GSTINs → BOOKS_ONLY + TWOB_ONLY."""
        results = run_matching(
            [pr(gstin=GSTIN_A)],
            [b2b(gstin=GSTIN_B)],
        )
        statuses = {r["status"] for r in results}
        assert "BOOKS_ONLY" in statuses
        assert "TWOB_ONLY" in statuses

    def test_below_fuzzy_threshold(self):
        """Similar GSTIN, matching amounts, but inv_no similarity < 60% → BOOKS_ONLY."""
        pr_inv  = "INVOICEABC123"
        b2b_inv = "XYZDEF456789"
        score = SequenceMatcher(None, pr_inv, b2b_inv).ratio()
        assert score < _FUZZY_THRESHOLD, (
            f"Test setup broken: similarity {score:.3f} >= threshold {_FUZZY_THRESHOLD}"
        )
        r = single(pr(inv=pr_inv), b2b(inv=b2b_inv))
        assert r["status"] == "BOOKS_ONLY"

    def test_multiple_pr_unmatched(self):
        """Three PR entries, zero 2B entries → three BOOKS_ONLY rows."""
        results = run_matching(
            [pr("p1"), pr("p2"), pr("p3")],
            [],
        )
        assert len(results) == 3
        assert all(r["status"] == "BOOKS_ONLY" for r in results)


# ── TWOB_ONLY ─────────────────────────────────────────────────────────────────

class TestTwobOnly:
    def test_no_pr_entry_at_all(self):
        results = run_matching([], [b2b()])
        assert len(results) == 1
        r = results[0]
        assert r["status"] == "TWOB_ONLY"
        assert r["pr_entry_id"] is None
        assert r["gstr2b_entry_id"] == "b1"
        assert r["confidence"] == 1.0
        assert r["mismatched_fields"] == []

    def test_multiple_2b_unmatched(self):
        results = run_matching([], [b2b("b1"), b2b("b2"), b2b("b3")])
        assert len(results) == 3
        assert all(r["status"] == "TWOB_ONLY" for r in results)

    def test_one_matched_one_twob(self):
        """2B has two entries; PR matches only one → the other becomes TWOB_ONLY."""
        results = run_matching(
            [pr("p1", inv="INV001")],
            [b2b("b1", inv="INV001"), b2b("b2", inv="INV999")],
        )
        statuses = {r["status"] for r in results}
        assert "MATCHED" in statuses
        assert "TWOB_ONLY" in statuses
        twob = next(r for r in results if r["status"] == "TWOB_ONLY")
        assert twob["gstr2b_entry_id"] == "b2"


# ── Mixed batch ───────────────────────────────────────────────────────────────

class TestMixedBatch:
    def test_all_five_buckets_in_one_batch(self):
        """Single call produces all five status types."""
        pr_rows = [
            pr("p1", inv="INV001"),                        # → MATCHED with b1
            pr("p2", inv="INV002", tv="50000"),            # → MISMATCH with b2 (amount)
            pr("p3", inv="INV2024003"),                    # → PROBABLE with b3 (fuzzy inv_no)
            pr("p4", inv="INV004", gstin=GSTIN_B),        # → BOOKS_ONLY (no 2B for GSTIN_B)
        ]
        b2b_rows = [
            b2b("b1", inv="INV001"),                       # MATCHED with p1
            b2b("b2", inv="INV002", tv="50002"),           # MISMATCH with p2
            b2b("b3", inv="INV24003"),                     # PROBABLE with p3
            b2b("b5", inv="INV999", gstin=GSTIN_A),       # TWOB_ONLY (no PR for this)
        ]
        results = run_matching(pr_rows, b2b_rows)
        status_map = {r["pr_entry_id"] or r["gstr2b_entry_id"]: r["status"]
                      for r in results}

        assert status_map["p1"] == "MATCHED"
        assert status_map["p2"] == "MISMATCH"
        assert status_map["p3"] == "PROBABLE"
        assert status_map["p4"] == "BOOKS_ONLY"
        assert status_map["b5"] == "TWOB_ONLY"

    def test_empty_both_sides(self):
        assert run_matching([], []) == []

    def test_one_pr_one_2b_returns_exactly_two_rows_when_unrelated(self):
        """Unrelated PR + 2B → one BOOKS_ONLY + one TWOB_ONLY = 2 results."""
        results = run_matching(
            [pr(gstin=GSTIN_A, inv="INVOICEAAA")],
            [b2b(gstin=GSTIN_B, inv="INVOICEBBB")],
        )
        assert len(results) == 2
        assert {r["status"] for r in results} == {"BOOKS_ONLY", "TWOB_ONLY"}


# ── Tolerance boundary precision ──────────────────────────────────────────────

class TestAmountTolerance:
    @pytest.mark.parametrize("diff,expected_status", [
        ("0.00",  "MATCHED"),   # zero difference
        ("0.50",  "MATCHED"),   # half-rupee
        ("1.00",  "MATCHED"),   # exactly at tolerance (not strictly greater)
        ("1.01",  "MISMATCH"),  # one paisa over tolerance
        ("1.99",  "MISMATCH"),
        ("100.00","MISMATCH"),
    ])
    def test_taxable_value_boundary(self, diff: str, expected_status: str):
        pr_tv  = Decimal("10000.00")
        b2b_tv = pr_tv + Decimal(diff)
        r = single(pr(tv=str(pr_tv)), b2b(tv=str(b2b_tv)))
        assert r["status"] == expected_status, (
            f"diff=₹{diff}: expected {expected_status}, got {r['status']}"
        )

    @pytest.mark.parametrize("diff,expected_status", [
        ("1.00",  "MATCHED"),
        ("1.01",  "MISMATCH"),
    ])
    def test_cgst_boundary(self, diff: str, expected_status: str):
        r = single(
            pr(cgst=str(Decimal("900.00") + Decimal(diff))),
            b2b(cgst="900.00"),
        )
        assert r["status"] == expected_status


# ── Fuzzy threshold boundary ──────────────────────────────────────────────────

class TestFuzzyThreshold:
    def test_threshold_constant_is_60_percent(self):
        assert _FUZZY_THRESHOLD == pytest.approx(0.60)

    def test_identical_inv_no_takes_exact_path_not_fuzzy(self):
        """If norm_inv_no is identical, exact key match fires — never fuzzy."""
        r = single(pr(inv="SAMEINV"), b2b(inv="SAMEINV"))
        assert r["status"] == "MATCHED"  # not PROBABLE

    def test_completely_dissimilar_inv_no_gives_books_only(self):
        pr_inv  = "AAAA0001"
        b2b_inv = "ZZZZ9999"
        score = SequenceMatcher(None, pr_inv, b2b_inv).ratio()
        assert score < _FUZZY_THRESHOLD, f"Score {score} unexpectedly >= threshold"
        r = single(pr(inv=pr_inv), b2b(inv=b2b_inv))
        assert r["status"] == "BOOKS_ONLY"
