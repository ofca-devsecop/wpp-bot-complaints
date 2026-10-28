from datetime import UTC, datetime

from sicar_bot.adapters.db.models import Conversation
from sicar_bot.adapters.db.repositories import ConversationRepository, ReportRepository
from sicar_bot.services.maintenance import abandon_stale

JID = "5491155550000@s.whatsapp.net"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _draft(session, *, updated_at: datetime):
    reports = ReportRepository(session)
    report = reports.create_draft(JID)
    report.updated_at = updated_at
    conversations = ConversationRepository(session)
    conversations.attach(conversations.get_or_create(JID), report)
    session.commit()
    return report


def test_abandon_stale_marks_old_drafts_and_detaches(session_factory, session):
    report = _draft(session, updated_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC))

    count = abandon_stale(session_factory, now=NOW)

    assert count == 1
    session.expire_all()
    assert report.status == "abandoned"
    conversation = session.get(Conversation, JID)
    assert conversation.report_id is None


def test_abandon_stale_keeps_recent_drafts(session_factory, session):
    report = _draft(session, updated_at=datetime(2026, 10, 28, 11, 0, tzinfo=UTC))

    assert abandon_stale(session_factory, now=NOW) == 0
    session.expire_all()
    assert report.status == "draft"


def test_abandon_stale_keeps_submitted_reports(session_factory, session):
    reports = ReportRepository(session)
    report = reports.create_draft(JID)
    reports.submit(report, now=datetime(2026, 10, 1, tzinfo=UTC))
    report.updated_at = datetime(2026, 10, 1, tzinfo=UTC)
    session.commit()

    assert abandon_stale(session_factory, now=NOW) == 0
    session.expire_all()
    assert report.status == "submitted"
