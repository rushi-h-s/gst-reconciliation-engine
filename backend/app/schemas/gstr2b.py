from decimal import Decimal
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, field_validator
from app.schemas.invoice import validate_gstin, _to_decimal


class Gstr2bEntry(BaseModel):
    supplier_gstin: str
    supplier_name: Optional[str] = None
    inv_no: str
    inv_date: date
    taxable_value: Decimal
    cgst: Decimal = Decimal("0")
    sgst: Decimal = Decimal("0")
    igst: Decimal = Decimal("0")
    doc_type: Literal["invoice", "credit_note", "debit_note"] = "invoice"

    @field_validator("supplier_gstin", mode="before")
    @classmethod
    def _validate_gstin(cls, v: str) -> str:
        return validate_gstin(v)

    @field_validator("taxable_value", "cgst", "sgst", "igst", mode="before")
    @classmethod
    def _coerce_decimal(cls, v) -> Decimal:
        return _to_decimal(v)


class Gstr2bDocument(BaseModel):
    """Root envelope for a GSTR-2B JSON file as downloaded from the portal."""

    gstin: str
    fp: str  # filing period, e.g. "032024" (MMYYYY)
    entries: list[Gstr2bEntry] = []
