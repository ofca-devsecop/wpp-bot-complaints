"""Puerto de almacenamiento de evidencia (comprobantes)."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sicar_bot.ports.messaging import InboundMedia, MediaKind


class EvidenceError(RuntimeError):
    """La evidencia no se puede almacenar."""


class UnsupportedMedia(EvidenceError):
    """Tipo de archivo no permitido."""


class EvidenceTooLarge(EvidenceError):
    """Archivo por encima del límite permitido."""


@dataclass(frozen=True, slots=True)
class StoredEvidence:
    sha256: str
    storage_path: str
    mime_type: str
    kind: MediaKind
    size: int


class EvidenceStorePort(Protocol):
    """Guarda bytes de forma idempotente: el mismo contenido no se duplica."""

    def save(self, report_id: UUID, media: InboundMedia) -> StoredEvidence: ...
