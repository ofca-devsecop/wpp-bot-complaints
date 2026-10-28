"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORT_STATUS = ("draft", "submitted", "exported", "archived", "abandoned")
THREAT_TYPE = (
    "fraude_en_linea",
    "usurpacion_de_identidad",
    "acceso_ilegitimo",
    "phishing",
    "acoso",
    "otros",
)
SEVERITY = ("alta", "media", "baja")
ATTACK_MEDIUM = (
    "whatsapp",
    "sms",
    "llamada",
    "telegram",
    "instagram",
    "facebook",
    "correo",
    "sitio_falso",
    "otro",
)
PROVINCE = (
    "buenos_aires",
    "catamarca",
    "chaco",
    "chubut",
    "caba",
    "cordoba",
    "corrientes",
    "entre_rios",
    "formosa",
    "jujuy",
    "la_pampa",
    "la_rioja",
    "mendoza",
    "misiones",
    "neuquen",
    "rio_negro",
    "salta",
    "san_juan",
    "san_luis",
    "santa_cruz",
    "santa_fe",
    "santiago_del_estero",
    "tierra_del_fuego",
    "tucuman",
)
CONVERSATION_STATE = ("awaiting_first", "collecting", "confirming", "done")
SURVEY_FIELD = (
    "threat_type",
    "severity",
    "province",
    "locality",
    "attack_medium",
    "had_economic_loss",
    "description",
    "evidence",
)

PUBLIC_CODE_SEQUENCE = sa.Sequence("public_code_seq", start=1)


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=40,
    )


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(PUBLIC_CODE_SEQUENCE))

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("public_code", sa.String(32), nullable=True),
        sa.Column("wa_jid", sa.String(64), nullable=False),
        sa.Column("status", _enum(*REPORT_STATUS, name="report_status"), nullable=False),
        sa.Column("threat_type", _enum(*THREAT_TYPE, name="threat_type"), nullable=True),
        sa.Column("severity", _enum(*SEVERITY, name="severity"), nullable=True),
        sa.Column("province", _enum(*PROVINCE, name="province"), nullable=True),
        sa.Column("locality", sa.String(120), nullable=True),
        sa.Column("attack_medium", _enum(*ATTACK_MEDIUM, name="attack_medium"), nullable=True),
        sa.Column("had_economic_loss", sa.Boolean(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("public_code", name="uq_reports_public_code"),
    )
    op.create_index("ix_reports_wa_jid", "reports", ["wa_jid"])
    op.create_index("ix_reports_status", "reports", ["status"])

    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("vision_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('image', 'document')", name="ck_evidence_kind"),
    )
    op.create_index("ix_evidence_report_id", "evidence", ["report_id"])
    op.create_index("ix_evidence_sha256", "evidence", ["sha256"])

    op.create_table(
        "conversations",
        sa.Column("wa_jid", sa.String(64), primary_key=True),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "state",
            _enum(*CONVERSATION_STATE, name="conversation_state"),
            nullable=False,
        ),
        sa.Column("current_field", _enum(*SURVEY_FIELD, name="survey_field"), nullable=True),
        sa.Column("last_question_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "messages",
        sa.Column("wa_message_id", sa.String(128), primary_key=True),
        sa.Column("wa_jid", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text_redacted", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_messages_direction"),
    )
    op.create_index("ix_messages_wa_jid", "messages", ["wa_jid"])

    op.create_table(
        "llm_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_runs")
    op.drop_index("ix_messages_wa_jid", table_name="messages")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_index("ix_evidence_sha256", table_name="evidence")
    op.drop_index("ix_evidence_report_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_reports_status", table_name="reports")
    op.drop_index("ix_reports_wa_jid", table_name="reports")
    op.drop_table("reports")
    op.execute(sa.schema.DropSequence(PUBLIC_CODE_SEQUENCE))
