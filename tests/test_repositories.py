import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sicar_bot.adapters.db.models import Conversation, Message, Report
from sicar_bot.adapters.db.repositories import (
    ConversationRepository,
    MessageRepository,
    ReportRepository,
)
from sicar_bot.domain.catalog import AttackMedium, Province, ReportStatus, Severity, ThreatType
from sicar_bot.domain.survey import ConversationState, Field, ReportDraft, Survey

JID = "5491100000001@s.whatsapp.net"
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def test_report_roundtrip(session: Session) -> None:
    reports = ReportRepository(session)
    report = reports.create_draft(JID)
    draft = ReportDraft(
        threat_type=ThreatType.PHISHING,
        severity=Severity.ALTA,
        province=Province.CABA,
        locality="Palermo",
        attack_medium=AttackMedium.WHATSAPP,
        had_economic_loss=True,
        description="Me pidieron el código de verificación.",
    )

    reports.apply_draft(report, draft)
    session.expire_all()

    stored = session.get(Report, report.id)
    assert stored is not None
    assert stored.status is ReportStatus.DRAFT
    assert stored.threat_type is ThreatType.PHISHING
    assert stored.severity is Severity.ALTA
    assert stored.province is Province.CABA
    assert stored.locality == "Palermo"
    assert stored.attack_medium is AttackMedium.WHATSAPP
    assert stored.had_economic_loss is True
    assert stored.description == "Me pidieron el código de verificación."
    assert stored.public_code is None


def test_submit_assigns_unique_public_codes(session: Session) -> None:
    reports = ReportRepository(session)
    moment = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)

    first = reports.submit(reports.create_draft(JID), now=moment)
    second = reports.submit(reports.create_draft("5491100000002@s.whatsapp.net"), now=moment)

    assert re.fullmatch(r"SICAR-2026-\d{6}", first.public_code or "")
    assert first.public_code != second.public_code
    assert first.status is ReportStatus.SUBMITTED
    assert first.submitted_at == moment

    reports.submit(first, now=moment)
    assert first.public_code is not None
    assert first.public_code.startswith("SICAR-2026-")


def test_duplicate_inbound_message_is_ignored(session: Session) -> None:
    messages = MessageRepository(session)

    assert messages.record_inbound("msg-1", JID, "text", "hola") is True
    assert messages.record_inbound("msg-1", JID, "text", "hola") is False

    total = session.scalar(select(func.count()).select_from(Message))
    assert total == 1


def test_conversation_roundtrip_and_sync(session: Session) -> None:
    conversations = ConversationRepository(session)
    reports = ReportRepository(session)

    conversation = conversations.get_or_create(JID)
    assert conversation.state is ConversationState.AWAITING_FIRST
    assert conversations.get_or_create(JID).wa_jid == JID

    report = reports.create_draft(JID)
    conversations.attach(conversation, report)

    survey = Survey()
    survey.begin(ReportDraft(threat_type=ThreatType.ACOSO))
    conversations.sync(conversation, survey)

    session.expire_all()
    stored = session.get(Conversation, JID)
    assert stored is not None
    assert stored.report_id == report.id
    assert stored.state is ConversationState.COLLECTING
    assert stored.current_field is Field.SEVERITY

    conversations.reset(stored)
    session.expire_all()
    assert session.get(Conversation, JID).state is ConversationState.AWAITING_FIRST


def test_submitted_between_filters_by_status_and_range(session: Session) -> None:
    reports = ReportRepository(session)
    moment = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)

    submitted = reports.submit(reports.create_draft(JID), now=moment)
    reports.create_draft("5491100000003@s.whatsapp.net")

    found = reports.submitted_between(moment.replace(hour=0), moment.replace(hour=23, minute=59))
    assert [report.id for report in found] == [submitted.id]


def test_state_survives_new_session(engine) -> None:
    jid = f"{uuid.uuid4().hex}@s.whatsapp.net"

    with Session(engine) as writer:
        conversation = ConversationRepository(writer).get_or_create(jid)
        survey = Survey()
        survey.begin(ReportDraft(threat_type=ThreatType.OTROS))
        ConversationRepository(writer).sync(conversation, survey)
        writer.commit()

    with Session(engine) as reader:
        stored = ConversationRepository(reader).get(jid)
        assert stored is not None
        assert stored.state is ConversationState.COLLECTING
        assert stored.current_field is Field.SEVERITY

    with Session(engine) as cleanup:
        cleanup.execute(delete(Conversation).where(Conversation.wa_jid == jid))
        cleanup.commit()
