import re
from decimal import Decimal, InvalidOperation
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, field_validator, model_validator

GSTIN_RE = re.compile(r"^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin_checksum_valid(gstin: str) -> bool:
    """Modified Luhn mod-36 checksum for GSTIN."""
    factor = 2
    total = 0
    for c in gstin[:14]:
        if c not in _CHARSET:
            return False
        code_point = _CHARSET.index(c)
        addend = factor * code_point
        factor = 1 if factor == 2 else 2
        addend = (addend // 36) + (addend % 36)
        total += addend
    remainder = total % 36
    expected = _CHARSET[(36 - remainder) % 36]
    return gstin[14] == expected


def validate_gstin(v: str) -> str:
    v = v.strip().upper()
    if not GSTIN_RE.match(v):
        raise ValueError(f"Invalid GSTIN format: {v!r}")
    if not _gstin_checksum_valid(v):
        raise ValueError(f"GSTIN checksum mismatch: {v!r}")
    return v


def _to_decimal(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except InvalidOperation:
        raise ValueError(f"Cannot parse as decimal: {v!r}")


class ExtractedInvoice(BaseModel):
    """Structured output from the VLM extraction step.

    Must match this schema exactly — provider must not return free text.
    All money fields are Decimal; never float.
    """

    supplier_gstin: str
    supplier_name: str
    inv_no: str
    inv_date: date
    taxable_value: Decimal
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")
    is_rcm: bool = False
    doc_type: Literal["invoice", "credit_note", "debit_note"] = "invoice"
    confidence: float = 1.0

    @field_validator("supplier_gstin", mode="before")
    @classmethod
    def _validate_gstin(cls, v: str) -> str:
        return validate_gstin(v)

    @field_validator("doc_type", mode="before")
    @classmethod
    def _normalise_doc_type(cls, v: str) -> str:
        return v.lower().replace(" ", "_") if isinstance(v, str) else v

    @field_validator("taxable_value", "cgst", "sgst", "igst", mode="before")
    @classmethod
    def _coerce_decimal(cls, v) -> Decimal:
        return _to_decimal(v)

    @field_validator("taxable_value", "cgst", "sgst", "igst")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("Tax amounts cannot be negative")
        return v

    @model_validator(mode="after")
    def _validate_tax_split(self) -> "ExtractedInvoice":
        """CGST/SGST and IGST are mutually exclusive (intra- vs inter-state)."""
        has_dual = self.cgst > 0 or self.sgst > 0
        has_igst = self.igst > 0
        if has_dual and has_igst:
            raise ValueError("Invoice cannot carry both CGST/SGST and IGST")
        return self

    @property
    def total_tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    @property
    def invoice_value(self) -> Decimal:
        return self.taxable_value + self.total_tax

    @property
    def pan(self) -> str:
        return self.supplier_gstin[2:12]


class ExtractionJob(BaseModel):
    """Message schema written to / read from the pgmq queue."""

    org_id: str
    client_id: str
    period: str  # YYYY-MM
    storage_path: str
    image_hash: str
    extraction_id: str


class CorrectionRequest(BaseModel):
    corrected_amount: Optional[float] = None
    corrected_date: Optional[date] = None
    reason: Literal["DataEntry", "Vendor", "Rounding"]
    notes: Optional[str] = None
