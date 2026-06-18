from abc import ABC, abstractmethod
from app.schemas.invoice import ExtractedInvoice


class ExtractionProvider(ABC):
    """VLM extraction interface.

    Swap implementations (Ollama → vLLM, cloud API, etc.) by registering a
    different concrete class.  The only file that changes is the one that
    wires up the provider in app/main.py.
    """

    @abstractmethod
    async def extract(self, image_bytes: bytes) -> ExtractedInvoice:
        """Return structured invoice data extracted from *image_bytes*.

        Must always return an ExtractedInvoice or raise — never free text.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True when the backing service is reachable."""
        ...
