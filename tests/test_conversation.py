from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from sicar_bot.adapters.db.models import Evidence, LlmRun, Report
from sicar_bot.adapters.evidence import FilesystemEvidenceStore
from sicar_bot.domain import messages
from sicar_bot.domain.catalog import AttackMedium, Province, Severity, ThreatType
from sicar_bot.domain.extraction import Extraction, Guess
from sicar_bot.domain.survey import ConversationState, Field
from sicar_bot.ports.extractor import ExtractionResult, ExtractorError
from sicar_bot.ports.messaging import InboundMedia, InboundMessage, MediaKind
from sicar_bot.services.conversation import ConversationService

JID = "5491155550000@s.whatsapp.net"


class FakeMessaging:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def on_message(self, handler) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        raise NotImplementedError

    async def send_text(self, wa_jid: str, text: str) -> None:
        self.sent.append((wa_jid, text))

    async def stop(self) -> None:
        raise NotImplementedError


class FakeExtractor:
    def __init__(
        self, extraction: Extraction | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[str, list]] = []
        self._extraction = extraction or _empty()
        self._error = error

    async def extract(self, text: str, images=()) -> ExtractionResult:
        self.calls.append((text, list(images)))
        if self._error is not None:
            raise self._error
        return ExtractionResult(
            extraction=self._extraction,
            redacted_text=text,
            input_hash="hash",
            model="fake",
            prompt_version="test-v1",
        )


def _empty() -> Extraction:
    unknown = Guess(None, 0.0)
    return Extraction(
        threat_type=unknown,
        severity=unknown,
        province=unknown,
        locality=unknown,
        attack_medium=unknown,
        had_economic_loss=unknown,
        description=unknown,
    )


def _full() -> Extraction:
    return Extraction(
        threat_type=Guess(ThreatType.FRAUDE_EN_LINEA, 0.95),
        severity=Guess(Severity.MEDIA, 0.95),
        province=Guess(Province.CORDOBA, 0.95),
        locality=Guess("Villa Carlos Paz", 0.95),
        attack_medium=Guess(AttackMedium.WHATSAPP, 0.95),
        had_economic_loss=Guess(True, 0.95),
        description=Guess("Me pidieron un código y vaciaron la cuenta", 0.95),
        evidence_summary="transferencia de $150.000",
    )


def _inbound(
    text: str = "",
    *,
    media: InboundMedia | None = None,
    message_id: str = "MSG-1",
    jid: str = JID,
) -> InboundMessage:
    return InboundMessage(
        wa_jid=jid,
        message_id=message_id,
        timestamp=datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
        text=text or None,
        medias=(media,) if media is not None else (),
    )


@pytest.fixture
def messaging() -> FakeMessaging:
    return FakeMessaging()


@pytest.fixture
def service(tmp_path, session_factory, messaging) -> ConversationService:
    return ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )


async def test_greeting_sends_welcome_without_asking(service, messaging, session):
    await service.handle(_inbound("Hola"))

    assert messaging.sent == [(JID, messages.WELCOME)]
    session.expire_all()
    assert session.scalars(select(Report)).all() == []


async def test_first_message_asks_only_the_missing_field(
    tmp_path, session_factory, messaging, session
):
    extractor = FakeExtractor(
        Extraction(
            threat_type=Guess(ThreatType.PHISHING, 0.95),
            severity=Guess(Severity.ALTA, 0.95),
            province=Guess(Province.CORDOBA, 0.95),
            locality=Guess(None, 0.0),
            attack_medium=Guess(None, 0.0),
            had_economic_loss=Guess(None, 0.0),
            description=Guess("Me llegó un mail con un link falso", 0.95),
        )
    )
    service = ConversationService(
        session_factory=session_factory,
        extractor=extractor,
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await service.handle(_inbound("Me llegó un mail con un link falso"))

    assert messaging.sent[-1][1].endswith(messages.question(Field.LOCALITY))
    session.expire_all()
    report = session.scalars(select(Report)).one()
    assert report.threat_type is ThreatType.PHISHING
    assert report.locality is None
    assert session.scalars(select(LlmRun)).one().model == "fake"


async def test_extraction_failure_still_starts_the_survey(
    tmp_path, session_factory, messaging, session
):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(error=ExtractorError("boom")),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await service.handle(_inbound("me estafaron"))

    assert messaging.sent[-1][1].endswith(messages.question(Field.THREAT_TYPE))
    session.expire_all()
    assert session.scalars(select(Report)).one().threat_type is None


async def test_image_is_stored_and_sent_to_vision(tmp_path, session_factory, messaging, session):
    extractor = FakeExtractor()
    store = FilesystemEvidenceStore(tmp_path / "evidence")
    service = ConversationService(
        session_factory=session_factory,
        extractor=extractor,
        evidence_store=store,
        messaging=messaging,
    )
    media = InboundMedia(kind=MediaKind.IMAGE, mime_type="image/png", data=b"\x89PNG\r\n\x1a\nfoto")

    await service.handle(_inbound("mirá", media=media))

    session.expire_all()
    evidence = session.scalars(select(Evidence)).one()
    assert (store.root / evidence.storage_path).read_bytes() == media.data
    assert evidence.caption == "mirá"
    assert extractor.calls[-1][1][0].mime_type == "image/png"


async def test_duplicate_message_is_ignored(service, messaging):
    inbound = _inbound("Hola")

    await service.handle(inbound)
    await service.handle(inbound)

    assert len(messaging.sent) == 1


async def test_all_fields_extracted_still_asks_for_evidence(
    tmp_path, session_factory, messaging, session
):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(_full()),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await service.handle(_inbound("me estafaron por whatsapp en Carlos Paz"))

    assert messaging.sent[-1][1].endswith(messages.question(Field.EVIDENCE))
    session.expire_all()
    assert session.scalars(select(Report)).one().locality == "Villa Carlos Paz"


async def test_all_fields_plus_evidence_shows_the_summary(
    tmp_path, session_factory, messaging, session
):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(_full()),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )
    media = InboundMedia(kind=MediaKind.IMAGE, mime_type="image/jpeg", data=b"\xff\xd8foto")

    await service.handle(_inbound("me estafaron por whatsapp en Carlos Paz", media=media))

    reply = messaging.sent[-1][1]
    assert "Resumen de tu denuncia:" in reply
    assert "CONFIRMAR" in reply


async def test_unsupported_media_is_skipped(tmp_path, session_factory, messaging, session):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )
    media = InboundMedia(kind=MediaKind.IMAGE, mime_type="image/gif", data=b"GIF89a")

    await service.handle(_inbound("mirá", media=media))

    session.expire_all()
    assert session.scalars(select(Evidence)).all() == []
    assert messaging.sent[-1][1].endswith(messages.question(Field.THREAT_TYPE))


async def test_conversation_tracks_the_current_field(service, session):
    from sicar_bot.adapters.db.models import Conversation

    await service.handle(_inbound("me estafaron"))

    session.expire_all()
    conversation = session.get(Conversation, JID)
    assert conversation is not None
    assert conversation.state is ConversationState.COLLECTING
    assert conversation.current_field is Field.THREAT_TYPE
    assert conversation.report_id is not None


def _media(data: bytes = b"\x89PNG\r\n\x1a\nfoto") -> InboundMedia:
    return InboundMedia(kind=MediaKind.IMAGE, mime_type="image/png", data=data)


async def _say(service, messaging, text: str, *, message_id: str) -> str:
    await service.handle(_inbound(text, message_id=message_id))
    return messaging.sent[-1][1]


async def test_full_conversation_submits_and_returns_code(
    tmp_path, session_factory, messaging, session
):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    assert (await _say(service, messaging, "me estafaron", message_id="m1")).endswith(
        messages.question(Field.THREAT_TYPE)
    )
    assert (await _say(service, messaging, "1", message_id="m2")).endswith(
        messages.question(Field.SEVERITY)
    )
    assert (await _say(service, messaging, "alta", message_id="m3")).endswith(
        messages.question(Field.PROVINCE)
    )
    assert (await _say(service, messaging, "Córdoba", message_id="m4")).endswith(
        messages.question(Field.LOCALITY)
    )
    assert (await _say(service, messaging, "omitir", message_id="m5")).endswith(
        messages.question(Field.ATTACK_MEDIUM)
    )
    assert (await _say(service, messaging, "whatsapp", message_id="m6")).endswith(
        messages.question(Field.HAD_ECONOMIC_LOSS)
    )
    assert (await _say(service, messaging, "no", message_id="m7")).endswith(
        messages.question(Field.DESCRIPTION)
    )
    assert (await _say(service, messaging, "me pidieron un código", message_id="m8")).endswith(
        messages.question(Field.EVIDENCE)
    )
    assert "Resumen de tu denuncia:" in await _say(service, messaging, "no", message_id="m9")

    closing = await _say(service, messaging, "confirmar", message_id="m10")

    session.expire_all()
    report = session.scalars(select(Report)).one()
    assert report.status == "submitted"
    assert report.public_code is not None
    assert report.public_code.startswith("SICAR-")
    assert report.threat_type is ThreatType.FRAUDE_EN_LINEA
    assert report.severity is Severity.ALTA
    assert report.province is Province.CORDOBA
    assert report.description == "me pidieron un código"
    assert report.public_code in closing


async def test_invalid_answer_asks_again(tmp_path, session_factory, messaging, session):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron", message_id="m1")
    reply = await _say(service, messaging, "no sé", message_id="m2")

    assert reply.startswith("No entendí.")
    session.expire_all()
    from sicar_bot.adapters.db.models import Conversation

    conversation = session.get(Conversation, JID)
    assert conversation.current_field is Field.THREAT_TYPE
    assert conversation.retry_count == 1


async def test_correct_field_then_summary_again(tmp_path, session_factory, messaging, session):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(_full()),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron por whatsapp en Carlos Paz", message_id="m1")
    await _say(service, messaging, "no", message_id="m2")
    reply = await _say(service, messaging, "corregir provincia", message_id="m3")
    assert reply.endswith(messages.question(Field.PROVINCE))

    reply = await _say(service, messaging, "Mendoza", message_id="m4")
    assert "Mendoza" in reply
    assert "CONFIRMAR" in reply

    session.expire_all()
    assert session.scalars(select(Report)).one().province is Province.MENDOZA


async def test_optional_field_can_be_omitted(tmp_path, session_factory, messaging, session):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron", message_id="m1")
    await _say(service, messaging, "1", message_id="m2")
    await _say(service, messaging, "alta", message_id="m3")
    await _say(service, messaging, "Córdoba", message_id="m4")
    reply = await _say(service, messaging, "omitir", message_id="m5")

    assert reply.endswith(messages.question(Field.ATTACK_MEDIUM))
    session.expire_all()
    from sicar_bot.adapters.db.models import Conversation

    assert session.get(Conversation, JID).omitted_fields == ["locality"]


async def test_evidence_yes_then_image_advances(tmp_path, session_factory, messaging, session):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(_full()),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron por whatsapp en Carlos Paz", message_id="m1")
    assert (await _say(service, messaging, "sí", message_id="m2")) == messages.EVIDENCE_REQUEST

    await service.handle(_inbound("", media=_media(), message_id="m3"))

    assert "Resumen de tu denuncia:" in messaging.sent[-1][1]
    session.expire_all()
    assert len(session.scalars(select(Evidence)).all()) == 1


async def test_merged_photo_and_text_answers_and_keeps_the_evidence(
    tmp_path, session_factory, messaging, session
):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron", message_id="m1")
    await service.handle(_inbound("1", media=_media(), message_id="m2"))

    assert messaging.sent[-1][1].endswith(messages.question(Field.SEVERITY))
    session.expire_all()
    report = session.scalars(select(Report)).one()
    assert report.threat_type is ThreatType.FRAUDE_EN_LINEA
    assert len(session.scalars(select(Evidence)).all()) == 1


async def test_photo_then_text_at_the_evidence_step_counts_as_evidence(
    tmp_path, session_factory, messaging, session
):
    from sicar_bot.adapters.db.models import Conversation

    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(_full()),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron por whatsapp en Carlos Paz", message_id="m1")
    assert (await _say(service, messaging, "sí", message_id="m2")) == messages.EVIDENCE_REQUEST

    await service.handle(_inbound("no tengo más", media=_media(), message_id="m3"))

    assert "Resumen de tu denuncia:" in messaging.sent[-1][1]
    session.expire_all()
    conversation = session.get(Conversation, JID)
    assert conversation.evidence_skipped is False
    assert len(session.scalars(select(Evidence)).all()) == 1


async def test_done_conversation_does_not_open_a_new_report(
    tmp_path, session_factory, messaging, session
):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(_full()),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )

    await _say(service, messaging, "me estafaron por whatsapp en Carlos Paz", message_id="m1")
    await _say(service, messaging, "no", message_id="m2")
    await _say(service, messaging, "confirmar", message_id="m3")
    reply = await _say(service, messaging, "otra cosa", message_id="m4")

    assert reply == messages.ALREADY_REGISTERED
    session.expire_all()
    assert len(session.scalars(select(Report)).all()) == 1
