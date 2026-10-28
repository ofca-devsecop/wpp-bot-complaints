"""Almacenamiento de evidencia en disco, direccionado por contenido."""

import hashlib
import os
import tempfile
from pathlib import Path
from uuid import UUID

from sicar_bot.ports.evidence import (
    EvidenceError,
    EvidenceTooLarge,
    StoredEvidence,
    UnsupportedMedia,
)
from sicar_bot.ports.messaging import InboundMedia

DEFAULT_MAX_BYTES = 10 * 1024 * 1024

ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FilesystemEvidenceStore:
    """Guarda `root/<report_id>/<sha256><ext>` sin duplicar contenido."""

    def __init__(self, root: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    def save(self, report_id: UUID, media: InboundMedia) -> StoredEvidence:
        report_id = UUID(str(report_id))
        extension = ALLOWED_TYPES.get(media.mime_type)
        if extension is None:
            raise UnsupportedMedia(media.mime_type)
        if not media.data:
            raise EvidenceError("empty media")
        if len(media.data) > self._max_bytes:
            raise EvidenceTooLarge(len(media.data))

        digest = _sha256(media.data)
        directory = self._root / str(report_id)
        path = directory / f"{digest}{extension}"

        if not (path.exists() and _sha256_file(path) == digest):
            directory.mkdir(parents=True, exist_ok=True)
            self._write_atomic(path, media.data)

        return StoredEvidence(
            sha256=digest,
            storage_path=f"{report_id}/{digest}{extension}",
            mime_type=media.mime_type,
            kind=media.kind,
            size=len(media.data),
        )

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(data)
            temp_path = Path(temp.name)
        try:
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
