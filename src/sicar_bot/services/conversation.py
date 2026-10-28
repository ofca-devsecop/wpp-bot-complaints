"""Manejo de la conversación: primer contacto, encuesta y alta."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from sicar_bot.adapters.db.models import Evidence, LlmRun, Report, ReportStatus
from sicar_bot.adapters.db.repositories import (
    ConversationRepository,
    MessageRepository,
    ReportRepository,
)
from sicar_bot.domain import commands, messages
from sicar_bot.domain.catalog import normalize
from sicar_bot.domain.extraction import prefill
from sicar_bot.domain.redaction import redact
from sicar_bot.domain.survey import (
    FIELD_LABELS,
    MAX_EVIDENCE,
    ConversationState,
    Field,
    Outcome,
    ReportDraft,
    Step,
    Survey,
)
from sicar_bot.ports.evidence import EvidenceError, EvidenceStorePort
from sicar_bot.ports.extractor import (
    ExtractionResult,
    ExtractorError,
    ExtractorPort,
    ImagePart,
)
from sicar_bot.ports.messaging import InboundMessage, MediaKind, MessagingPort

log = logging.getLogger(__name__)

GREETINGS = frozenset(
    {"hola", "hola buenas", "buenas", "buen dia", "buenas tardes", "buenas noches", "hi"}
)
VISION_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
RATE_LIMIT = 20
RATE_WINDOW = timedelta(minutes=1)


def _message_kind(inbound: InboundMessage) -> str:
    if not inbound.has_media:
        return "text"
    if inbound.text:
        return "mixed"
    return inbound.medias[0].kind.value


def _has_content(inbound: InboundMessage) -> bool:
    if inbound.has_media:
        return True
    return normalize(inbound.text or "") not in GREETINGS


class ConversationService:
    """Traduce mensajes entrantes en pasos de la encuesta y respuestas."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        extractor: ExtractorPort,
        evidence_store: EvidenceStorePort,
        messaging: MessagingPort,
    ) -> None:
        self._session_factory = session_factory
        self._extractor = extractor
        self._store = evidence_store
        self._messaging = messaging

    async def handle(self, inbound: InboundMessage) -> None:
        redacted = redact(inbound.text or "")
        with self._session_factory() as session:
            messages_repo = MessageRepository(session)
            if not messages_repo.record_inbound(
                inbound.message_id,
                inbound.wa_jid,
                _message_kind(inbound),
                redacted or None,
            ):
                return
            if (
                messages_repo.count_inbound_since(inbound.wa_jid, datetime.now(UTC) - RATE_WINDOW)
                > RATE_LIMIT
            ):
                log.warning("rate limit hit", extra={"wa_jid": inbound.wa_jid})
                return

            conversations = ConversationRepository(session)
            conversation = conversations.get_or_create(inbound.wa_jid)
            command = commands.parse(inbound.text or "") if not inbound.has_media else None
            if command is not None:
                reply = self._command(session, conversation, conversations, command)
            elif (
                conversation.report_id is None
                and conversation.state is ConversationState.AWAITING_FIRST
            ):
                reply = await self._first_contact(session, conversation, inbound, conversations)
            else:
                reply = await self._continue(session, conversation, inbound, conversations)
            session.commit()

        if reply:
            await self._messaging.send_text(inbound.wa_jid, reply)

    def _command(
        self,
        session: Session,
        conversation,
        conversations: ConversationRepository,
        command: commands.Command,
    ) -> str:
        reports = ReportRepository(session)
        report = reports.get(conversation.report_id) if conversation.report_id else None

        if command is commands.Command.HELP:
            return messages.HELP
        if command is commands.Command.STATUS:
            if report is None:
                return messages.STATUS_EMPTY
            if report.status is ReportStatus.DRAFT:
                field = conversation.current_field
                pending = FIELD_LABELS[field] if field is not None else "datos"
                return messages.STATUS_PROGRESS.format(campo=pending)
            return messages.STATUS_SUBMITTED.format(codigo=report.public_code)

        if report is not None:
            reports.abandon(report)
        conversations.reset(conversation)
        if command is commands.Command.RESTART:
            return messages.WELCOME
        return messages.CANCELLED

    async def _first_contact(
        self,
        session: Session,
        conversation,
        inbound: InboundMessage,
        conversations: ConversationRepository,
    ) -> str:
        if not _has_content(inbound):
            return messages.WELCOME

        reports = ReportRepository(session)
        report = reports.create_draft(inbound.wa_jid)
        draft = ReportDraft(evidence_count=self._store_medias(session, report, inbound))
        result = await self._extract(inbound)
        if result is not None:
            prefill(draft, result.extraction)
            report.evidence_summary = result.extraction.evidence_summary
            self._record_llm_run(session, report, result)

        reports.apply_draft(report, draft)
        survey = Survey()
        step = survey.begin(draft)
        conversations.attach(conversation, report)
        conversations.sync(conversation, survey)
        return f"{messages.WELCOME}\n\n{self._render(step, survey, report)}"

    async def _continue(
        self,
        session: Session,
        conversation,
        inbound: InboundMessage,
        conversations: ConversationRepository,
    ) -> str:
        reports = ReportRepository(session)
        report = reports.get(conversation.report_id) if conversation.report_id else None
        if report is None:
            conversations.reset(conversation)
            return messages.WELCOME

        draft = reports.to_draft(report)
        draft.evidence_skipped = conversation.evidence_skipped
        draft.omitted = {Field(value) for value in conversation.omitted_fields}
        survey = Survey.restore(
            state=conversation.state,
            draft=draft,
            current_field=conversation.current_field,
            retry_count=conversation.retry_count,
        )
        if survey.state is ConversationState.DONE:
            return messages.ALREADY_REGISTERED
        if survey.state is ConversationState.AWAITING_FIRST:
            return messages.WELCOME
        if report.status is ReportStatus.ABANDONED:
            conversations.reset(conversation)
            return messages.WELCOME

        room = max(MAX_EVIDENCE - draft.evidence_count, 0)
        if inbound.has_media and survey.current_field is Field.EVIDENCE:
            if not self._store_medias(session, report, inbound, limit=room):
                return messages.EVIDENCE_REJECTED
            step = survey.add_evidence()
        elif inbound.text:
            self._store_medias(session, report, inbound, limit=room)
            step = survey.submit(self._answer(inbound, survey.current_field))
        elif inbound.has_media:
            if not self._store_medias(session, report, inbound, limit=room):
                return messages.EVIDENCE_REJECTED
            step = survey.add_evidence()
        else:
            return ""

        reports.apply_draft(report, survey.draft)
        if step.outcome is Outcome.DONE:
            reports.submit(report)
        conversations.sync(conversation, survey)
        return self._render(step, survey, report)

    @staticmethod
    def _answer(inbound: InboundMessage, field: Field | None) -> str:
        raw = inbound.text or ""
        if field is not None:
            translated = messages.choice(field, raw)
            if translated is not None:
                return translated
        return raw

    def _render(self, step: Step, survey: Survey, report: Report) -> str:
        if step.outcome is Outcome.PROMPT and step.field is not None:
            return messages.question(step.field)
        if step.outcome is Outcome.RETRY:
            if step.field is None:
                return f"No entendí.\n\n{messages.summary(survey)}"
            return messages.question(step.field, retry=True)
        if step.outcome is Outcome.AWAITING_EVIDENCE:
            return messages.EVIDENCE_REQUEST
        if step.outcome is Outcome.EVIDENCE_FULL:
            return messages.EVIDENCE_FULL
        if step.outcome is Outcome.CONFIRM:
            return messages.summary(survey)
        if step.outcome is Outcome.DONE:
            return messages.CLOSING.format(codigo=report.public_code)
        return ""

    def _store_medias(
        self,
        session: Session,
        report: Report,
        inbound: InboundMessage,
        *,
        limit: int = MAX_EVIDENCE,
    ) -> int:
        stored_count = 0
        for media in inbound.medias:
            if stored_count >= limit:
                log.warning("evidence ignored: limit reached", extra={"report_id": report.id})
                break
            try:
                stored = self._store.save(report.id, media)
            except EvidenceError as error:
                log.warning(
                    "evidence rejected", extra={"mime": media.mime_type, "error": str(error)}
                )
                continue
            session.add(
                Evidence(
                    report_id=report.id,
                    kind=stored.kind.value,
                    mime_type=stored.mime_type,
                    sha256=stored.sha256,
                    storage_path=stored.storage_path,
                    caption=media.caption or inbound.text,
                )
            )
            stored_count += 1
        return stored_count

    async def _extract(self, inbound: InboundMessage) -> ExtractionResult | None:
        images = [
            ImagePart(mime_type=media.mime_type, data=media.data)
            for media in inbound.medias
            if media.kind is MediaKind.IMAGE and media.mime_type in VISION_TYPES
        ]
        text = redact(inbound.text or "")
        if not text.strip() and not images:
            return None
        try:
            return await self._extractor.extract(text, images)
        except ExtractorError:
            log.warning("extraction failed; continuing without prefill", exc_info=True)
            return None

    @staticmethod
    def _record_llm_run(session: Session, report: Report, result: ExtractionResult) -> None:
        session.add(
            LlmRun(
                report_id=report.id,
                prompt_version=result.prompt_version,
                model=result.model,
                input_hash=result.input_hash,
                output_json={"evidence_summary": result.extraction.evidence_summary},
            )
        )
