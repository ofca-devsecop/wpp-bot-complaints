import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sicar_bot.adapters.db.models import (
    PUBLIC_CODE_SEQUENCE,
    Conversation,
    Evidence,
    Message,
    Report,
)
from sicar_bot.config import get_settings
from sicar_bot.domain.catalog import ReportStatus
from sicar_bot.domain.survey import ConversationState, ReportDraft, Survey


def now_local() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


class ReportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_draft(self, wa_jid: str) -> Report:
        report = Report(wa_jid=wa_jid, status=ReportStatus.DRAFT)
        self.session.add(report)
        self.session.flush()
        return report

    def get(self, report_id: uuid.UUID) -> Report | None:
        return self.session.get(Report, report_id)

    def active_for(self, wa_jid: str) -> Report | None:
        stmt = (
            select(Report)
            .where(Report.wa_jid == wa_jid, Report.status == ReportStatus.DRAFT)
            .order_by(Report.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def to_draft(self, report: Report) -> ReportDraft:
        count = self.session.execute(
            select(func.count()).select_from(Evidence).where(Evidence.report_id == report.id)
        ).scalar_one()
        return ReportDraft(
            threat_type=report.threat_type,
            severity=report.severity,
            province=report.province,
            locality=report.locality,
            attack_medium=report.attack_medium,
            had_economic_loss=report.had_economic_loss,
            description=report.description,
            evidence_count=count,
        )

    def apply_draft(self, report: Report, draft: ReportDraft) -> Report:
        report.threat_type = draft.threat_type
        report.severity = draft.severity
        report.province = draft.province
        report.locality = draft.locality
        report.attack_medium = draft.attack_medium
        report.had_economic_loss = draft.had_economic_loss
        report.description = draft.description
        self.session.flush()
        return report

    def submit(self, report: Report, *, now: datetime | None = None) -> Report:
        if report.status is not ReportStatus.DRAFT:
            return report
        moment = now or now_local()
        sequence = self.session.execute(select(PUBLIC_CODE_SEQUENCE.next_value())).scalar_one()
        report.public_code = f"SICAR-{moment.year}-{sequence:06d}"
        report.status = ReportStatus.SUBMITTED
        report.submitted_at = moment
        self.session.flush()
        return report

    def abandon(self, report: Report, *, now: datetime | None = None) -> Report:
        if report.status is ReportStatus.DRAFT:
            report.status = ReportStatus.ABANDONED
            report.updated_at = now or now_local()
            self.session.flush()
        return report

    def abandon_stale(self, cutoff: datetime) -> int:
        result = self.session.execute(
            update(Report)
            .where(Report.status == ReportStatus.DRAFT, Report.updated_at < cutoff)
            .values(status=ReportStatus.ABANDONED, updated_at=now_local())
            .returning(Report.id)
        )
        return len(result.all())

    def submitted_between(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[Report]:
        stmt = select(Report).where(
            Report.status.in_([ReportStatus.SUBMITTED, ReportStatus.EXPORTED])
        )
        if start is not None:
            stmt = stmt.where(Report.submitted_at >= start)
        if end is not None:
            stmt = stmt.where(Report.submitted_at < end)
        return list(self.session.scalars(stmt.order_by(Report.submitted_at)))


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, wa_jid: str) -> Conversation | None:
        return self.session.get(Conversation, wa_jid)

    def get_or_create(self, wa_jid: str) -> Conversation:
        self.session.execute(
            insert(Conversation)
            .values(wa_jid=wa_jid, state=ConversationState.AWAITING_FIRST)
            .on_conflict_do_nothing(index_elements=[Conversation.wa_jid])
        )
        conversation = self.session.get(Conversation, wa_jid)
        assert conversation is not None
        return conversation

    def attach(self, conversation: Conversation, report: Report) -> None:
        conversation.report_id = report.id
        self.session.flush()

    def sync(self, conversation: Conversation, survey: Survey) -> None:
        conversation.state = survey.state
        conversation.current_field = survey.current_field
        conversation.retry_count = survey.retry_count
        conversation.evidence_skipped = survey.draft.evidence_skipped
        conversation.omitted_fields = sorted(field.value for field in survey.draft.omitted)
        conversation.last_activity_at = now_local()
        self.session.flush()

    def reset(self, conversation: Conversation) -> None:
        conversation.report_id = None
        conversation.state = ConversationState.AWAITING_FIRST
        conversation.current_field = None
        conversation.retry_count = 0
        conversation.evidence_skipped = False
        conversation.omitted_fields = []
        self.session.flush()

    def detach_abandoned(self) -> None:
        abandoned = select(Report.id).where(Report.status == ReportStatus.ABANDONED)
        self.session.execute(
            update(Conversation)
            .where(Conversation.report_id.in_(abandoned))
            .values(
                report_id=None,
                state=ConversationState.AWAITING_FIRST,
                current_field=None,
                retry_count=0,
                evidence_skipped=False,
                omitted_fields=[],
            )
        )


class MessageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        wa_message_id: str,
        wa_jid: str,
        direction: str,
        kind: str,
        text_redacted: str | None = None,
    ) -> bool:
        result = self.session.execute(
            insert(Message)
            .values(
                wa_message_id=wa_message_id,
                wa_jid=wa_jid,
                direction=direction,
                kind=kind,
                text_redacted=text_redacted,
            )
            .on_conflict_do_nothing(index_elements=[Message.wa_message_id])
            .returning(Message.wa_message_id)
        )
        return result.first() is not None

    def count_inbound_since(self, wa_jid: str, since: datetime) -> int:
        return self.session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.wa_jid == wa_jid,
                Message.direction == "in",
                Message.created_at >= since,
            )
        ).scalar_one()

    def record_inbound(
        self,
        wa_message_id: str,
        wa_jid: str,
        kind: str,
        text_redacted: str | None = None,
    ) -> bool:
        return self.record(
            wa_message_id=wa_message_id,
            wa_jid=wa_jid,
            direction="in",
            kind=kind,
            text_redacted=text_redacted,
        )
