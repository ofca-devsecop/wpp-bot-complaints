from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from sicar_bot.adapters.db.models import Conversation, Report
from sicar_bot.adapters.evidence import FilesystemEvidenceStore
from sicar_bot.domain import messages
from sicar_bot.domain.catalog import AttackMedium, Province, Severity, ThreatType
from sicar_bot.domain.commands import Command, parse
from sicar_bot.domain.extraction import Extraction, Guess
from sicar_bot.domain.survey import ConversationState, Field
from sicar_bot.ports.extractor import ExtractionResult
from sicar_bot.ports.messaging import InboundMessage
from sicar_bot.services.conversation import RATE_LIMIT, ConversationService

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
    def __init__(self) -> None:
        self._extraction = Extraction(
            threat_type=Guess(None, 0.0),
            severity=Guess(None, 0.0),
            province=Guess(None, 0.0),
            locality=Guess(None, 0.0),
            attack_medium=Guess(None, 0.0),
            had_economic_loss=Guess(None, 0.0),
            description=Guess(None, 0.0),
        )

    async def extract(self, text: str, images=()) -> ExtractionResult:
        return ExtractionResult(
            extraction=self._extraction,
            redacted_text=text,
            input_hash="hash",
            model="fake",
            prompt_version="test-v1",
        )


def _inbound(text: str, message_id: str) -> InboundMessage:
    return InboundMessage(
        wa_jid=JID,
        message_id=message_id,
        timestamp=datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
        text=text or None,
        media=None,
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


async def _say(service, messaging, text: str, message_id: str) -> str:
    await service.handle(_inbound(text, message_id))
    return messaging.sent[-1][1]


def test_parse_accepts_accents_and_slashes() -> None:
    assert parse("AYUDA") is Command.HELP
    assert parse("/ayuda") is Command.HELP
    assert parse("estado") is Command.STATUS
    assert parse("cancelar") is Command.CANCEL
    assert parse("Reiniciar") is Command.RESTART
    assert parse("me estafaron") is None
    assert parse("") is None


async def test_help_works_without_a_conversation(service, messaging, session):
    assert await _say(service, messaging, "ayuda", "m1") == messages.HELP
    session.expire_all()
    assert session.scalars(select(Report)).all() == []


async def test_status_reports_progress(service, messaging, session):
    await _say(service, messaging, "me estafaron", "m1")
    reply = await _say(service, messaging, "estado", "m2")

    assert "borrador" in reply
    assert Field.THREAT_TYPE.value not in reply
    session.expire_all()
    assert session.get(Conversation, JID).current_field is Field.THREAT_TYPE


async def test_status_before_starting(service, messaging):
    assert await _say(service, messaging, "estado", "m1") == messages.STATUS_EMPTY


async def test_cancel_abandons_the_draft_and_resets(service, messaging, session):
    await _say(service, messaging, "me estafaron", "m1")
    assert await _say(service, messaging, "cancelar", "m2") == messages.CANCELLED

    session.expire_all()
    assert session.scalars(select(Report)).one().status == "abandoned"
    conversation = session.get(Conversation, JID)
    assert conversation.report_id is None
    assert conversation.state is ConversationState.AWAITING_FIRST

    assert (await _say(service, messaging, "me estafaron de nuevo", "m3")).endswith(
        messages.question(Field.THREAT_TYPE)
    )
    session.expire_all()
    assert len(session.scalars(select(Report)).all()) == 2


async def test_restart_abandons_and_welcomes(service, messaging, session):
    await _say(service, messaging, "me estafaron", "m1")
    assert await _say(service, messaging, "reiniciar", "m2") == messages.WELCOME

    session.expire_all()
    assert session.scalars(select(Report)).one().status == "abandoned"
    assert session.get(Conversation, JID).report_id is None


async def test_status_after_submit_shows_the_code(tmp_path, session_factory, messaging, session):
    service = ConversationService(
        session_factory=session_factory,
        extractor=FakeExtractor(),
        evidence_store=FilesystemEvidenceStore(tmp_path / "evidence"),
        messaging=messaging,
    )
    await _say(service, messaging, "me estafaron", "m1")
    for index, answer in enumerate(["1", "alta", "Córdoba", "omitir", "whatsapp", "no"], 2):
        await _say(service, messaging, answer, f"m{index}")
    await _say(service, messaging, "me pidieron un código", "m8")
    await _say(service, messaging, "no", "m9")
    await _say(service, messaging, "confirmar", "m10")

    session.expire_all()
    report = session.scalars(select(Report)).one()
    reply = await _say(service, messaging, "estado", "m11")

    assert reply == messages.STATUS_SUBMITTED.format(codigo=report.public_code)
    assert report.threat_type is ThreatType.FRAUDE_EN_LINEA
    assert report.severity is Severity.ALTA
    assert report.province is Province.CORDOBA
    assert report.attack_medium is AttackMedium.WHATSAPP


async def test_rate_limit_drops_excess_messages(service, messaging):
    for index in range(RATE_LIMIT + 1):
        await service.handle(_inbound("ayuda", f"m{index}"))

    assert len(messaging.sent) == RATE_LIMIT
