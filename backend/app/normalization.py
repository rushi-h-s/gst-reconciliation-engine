import re

# Leading-zero handling: normalize but flag for human review when ambiguous.
_INV_STRIP = re.compile(r"[\s\-/]")


def normalize_invoice_number(inv_no: str) -> str:
    return _INV_STRIP.sub("", inv_no.upper())


def normalize_gstin(gstin: str) -> str:
    return gstin.strip().upper()


def pan_from_gstin(gstin: str) -> str:
    """Chars 3–12 (0-indexed 2:12) of a valid 15-char GSTIN."""
    return gstin[2:12] if len(gstin) == 15 else ""
