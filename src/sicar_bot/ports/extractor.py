from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sicar_bot.domain.extraction import Extraction, ReceiptExtraction


class ExtractorError(RuntimeError):
    """El extractor no pudo producir una extracción válida."""


@dataclass(frozen=True)
class ImagePart:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class ExtractionResult:
    extraction: Extraction
    redacted_text: str
    input_hash: str
    model: str
    prompt_version: str


class ExtractorPort(Protocol):
    async def extract(self, text: str, images: Sequence[ImagePart] = ()) -> ExtractionResult: ...


class ReceiptExtractorPort(Protocol):
    """Devuelve `None` cuando no hay imágenes o el proveedor falla: nunca bloquea."""

    async def extract_receipt(self, images: Sequence[ImagePart]) -> ReceiptExtraction | None: ...
