import hashlib
from uuid import uuid4

import pytest

from sicar_bot.adapters.evidence import FilesystemEvidenceStore
from sicar_bot.ports.evidence import EvidenceError, EvidenceTooLarge, UnsupportedMedia
from sicar_bot.ports.messaging import InboundMedia, MediaKind

PNG = b"\x89PNG\r\n\x1a\nfoto"


def _media(data: bytes = PNG, mime_type: str = "image/png") -> InboundMedia:
    return InboundMedia(kind=MediaKind.IMAGE, mime_type=mime_type, data=data)


def test_saves_content_addressed_file(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)
    report_id = uuid4()

    stored = store.save(report_id, _media())

    digest = hashlib.sha256(PNG).hexdigest()
    assert stored.sha256 == digest
    assert stored.storage_path == f"{report_id}/{digest}.png"
    assert stored.size == len(PNG)
    assert stored.mime_type == "image/png"
    assert (tmp_path / stored.storage_path).read_bytes() == PNG


def test_same_content_is_not_duplicated(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)
    report_id = uuid4()

    first = store.save(report_id, _media())
    second = store.save(report_id, _media())

    assert first == second
    assert len(list((tmp_path / str(report_id)).iterdir())) == 1


def test_different_content_creates_new_file(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)
    report_id = uuid4()

    first = store.save(report_id, _media())
    second = store.save(report_id, _media(b"\x89PNG\r\n\x1a\x0b area"))

    assert first.storage_path != second.storage_path
    assert len(list((tmp_path / str(report_id)).iterdir())) == 2


def test_rejects_unsupported_type(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)

    with pytest.raises(UnsupportedMedia):
        store.save(uuid4(), _media(mime_type="image/gif"))


def test_rejects_oversized_file(tmp_path):
    store = FilesystemEvidenceStore(tmp_path, max_bytes=4)

    with pytest.raises(EvidenceTooLarge):
        store.save(uuid4(), _media())


def test_rejects_empty_file(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)

    with pytest.raises(EvidenceError):
        store.save(uuid4(), _media(b""))


def test_rejects_report_id_that_is_not_a_uuid(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)

    with pytest.raises(ValueError):
        store.save("../../etc", _media())


def test_rewrites_corrupted_existing_file(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)
    report_id = uuid4()
    stored = store.save(report_id, _media())
    path = tmp_path / stored.storage_path
    path.write_bytes(b"corrupto")

    store.save(report_id, _media())

    assert path.read_bytes() == PNG


def test_pdf_is_accepted(tmp_path):
    store = FilesystemEvidenceStore(tmp_path)

    stored = store.save(
        uuid4(),
        InboundMedia(
            kind=MediaKind.DOCUMENT,
            mime_type="application/pdf",
            data=b"%PDF-1.4",
        ),
    )

    assert stored.storage_path.endswith(".pdf")
    assert stored.kind is MediaKind.DOCUMENT
