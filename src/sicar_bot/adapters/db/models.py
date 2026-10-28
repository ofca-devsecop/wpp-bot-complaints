import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Sequence,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sicar_bot.adapters.db.base import Base
from sicar_bot.domain.catalog import AttackMedium, Province, ReportStatus, Severity, ThreatType
from sicar_bot.domain.survey import ConversationState, Field

PUBLIC_CODE_SEQUENCE = Sequence("public_code_seq", start=1)


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=40,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    public_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    wa_jid: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status"), default=ReportStatus.DRAFT, index=True
    )
    threat_type: Mapped[ThreatType | None] = mapped_column(_enum(ThreatType, "threat_type"))
    severity: Mapped[Severity | None] = mapped_column(_enum(Severity, "severity"))
    province: Mapped[Province | None] = mapped_column(_enum(Province, "province"))
    locality: Mapped[str | None] = mapped_column(String(120))
    attack_medium: Mapped[AttackMedium | None] = mapped_column(_enum(AttackMedium, "attack_medium"))
    had_economic_loss: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(Text)
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (CheckConstraint("kind IN ('image', 'document')", name="ck_evidence_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    vision_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report: Mapped[Report] = relationship(back_populates="evidence")


class Conversation(Base):
    __tablename__ = "conversations"

    wa_jid: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL")
    )
    state: Mapped[ConversationState] = mapped_column(
        _enum(ConversationState, "conversation_state"), default=ConversationState.AWAITING_FIRST
    )
    current_field: Mapped[Field | None] = mapped_column(_enum(Field, "survey_field"))
    last_question_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    omitted_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("direction IN ('in', 'out')", name="ck_messages_direction"),)

    wa_message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    wa_jid: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    text_redacted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmRun(Base):
    __tablename__ = "llm_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL")
    )
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
