import base64
import json
import httpx
from app.config import settings
from app.extraction.provider import ExtractionProvider
from app.schemas.invoice import ExtractedInvoice

_EXTRACTION_PROMPT = """\
You are a GST invoice data extraction assistant.
Extract ALL fields from the invoice image and return ONLY a valid JSON object — \
no explanation, no markdown fences.

Required JSON structure:
{
  "supplier_gstin": "<15-char GSTIN>",
  "supplier_name": "<string>",
  "inv_no": "<invoice number>",
  "inv_date": "<YYYY-MM-DD>",
  "taxable_value": "<decimal in INR>",
  "cgst": "<decimal in INR, 0 if absent>",
  "sgst": "<decimal in INR, 0 if absent>",
  "igst": "<decimal in INR, 0 if absent>",
  "is_rcm": <true|false>,
  "doc_type": "<invoice|credit_note|debit_note>",
  "confidence": <0.0-1.0>
}
"""


class OllamaExtractionProvider(ExtractionProvider):
    """Calls Ollama's /api/generate endpoint with qwen2.5vl:7b."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model
        self._client = httpx.AsyncClient(timeout=300.0)

    async def extract(self, image_bytes: bytes) -> ExtractedInvoice:
        b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "model": self._model,
            "prompt": _EXTRACTION_PROMPT,
            "images": [b64],
            "stream": False,
            "format": "json",
        }
        response = await self._client.post(f"{self._base_url}/api/generate", json=payload)
        response.raise_for_status()
        raw_json = response.json()["response"]
        data = json.loads(raw_json)
        return ExtractedInvoice(**data)

    async def health_check(self) -> bool:
        try:
            r = await self._client.get(f"{self._base_url}/api/tags", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False
