"""Máquina de estados pura de la encuesta. Sin I/O ni textos de WhatsApp."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum

from sicar_bot.domain.catalog import (
    AttackMedium,
    CatalogError,
    Province,
    Severity,
    ThreatType,
    build_index,
    match_option,
    normalize,
    resolve_attack_medium,
    resolve_province,
    resolve_severity,
    resolve_threat_type,
)


class ConversationState(StrEnum):
    AWAITING_FIRST = "awaiting_first"
    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    DONE = "done"


class Field(StrEnum):
    THREAT_TYPE = "threat_type"
    SEVERITY = "severity"
    PROVINCE = "province"
    LOCALITY = "locality"
    ATTACK_MEDIUM = "attack_medium"
    HAD_ECONOMIC_LOSS = "had_economic_loss"
    DESCRIPTION = "description"
    EVIDENCE = "evidence"


FIELD_ORDER: tuple[Field, ...] = (
    Field.THREAT_TYPE,
    Field.SEVERITY,
    Field.PROVINCE,
    Field.LOCALITY,
    Field.ATTACK_MEDIUM,
    Field.HAD_ECONOMIC_LOSS,
    Field.DESCRIPTION,
    Field.EVIDENCE,
)

REQUIRED_FIELDS: frozenset[Field] = frozenset(
    {Field.THREAT_TYPE, Field.SEVERITY, Field.PROVINCE, Field.DESCRIPTION}
)
OPTIONAL_FIELDS: frozenset[Field] = frozenset(FIELD_ORDER) - REQUIRED_FIELDS

FIELD_LABELS: dict[Field, str] = {
    Field.THREAT_TYPE: "Tipo de amenaza",
    Field.SEVERITY: "Severidad",
    Field.PROVINCE: "Provincia",
    Field.LOCALITY: "Localidad",
    Field.ATTACK_MEDIUM: "Medio del atacante",
    Field.HAD_ECONOMIC_LOSS: "Pérdida económica",
    Field.DESCRIPTION: "Descripción",
    Field.EVIDENCE: "Evidencia",
}

FIELD_ALIASES: dict[str, Field] = {
    "tipo": Field.THREAT_TYPE,
    "tipo de amenaza": Field.THREAT_TYPE,
    "amenaza": Field.THREAT_TYPE,
    "severidad": Field.SEVERITY,
    "gravedad": Field.SEVERITY,
    "provincia": Field.PROVINCE,
    "localidad": Field.LOCALITY,
    "medio": Field.ATTACK_MEDIUM,
    "medio del atacante": Field.ATTACK_MEDIUM,
    "perdida economica": Field.HAD_ECONOMIC_LOSS,
    "perdida": Field.HAD_ECONOMIC_LOSS,
    "descripcion": Field.DESCRIPTION,
    "relato": Field.DESCRIPTION,
    "evidencia": Field.EVIDENCE,
    "archivos": Field.EVIDENCE,
    "adjuntos": Field.EVIDENCE,
}

_FIELD_INDEX = build_index(
    [(field.value, field) for field in Field]
    + [(label, field) for field, label in FIELD_LABELS.items()]
    + list(FIELD_ALIASES.items())
)

_TRUE = frozenset({"si", "s", "yes", "true", "1"})
_FALSE = frozenset({"no", "n", "false", "0", "nunca"})
MAX_EVIDENCE = 5

_EVIDENCE_YES = frozenset({"si", "s"})
_EVIDENCE_NO = frozenset({"no", "n", "omitir"})


def resolve_field(raw: str) -> Field:
    return match_option(_FIELD_INDEX, raw)


@dataclass
class ReportDraft:
    threat_type: ThreatType | None = None
    severity: Severity | None = None
    province: Province | None = None
    locality: str | None = None
    attack_medium: AttackMedium | None = None
    had_economic_loss: bool | None = None
    description: str | None = None
    evidence_count: int = 0
    evidence_skipped: bool = False
    omitted: set[Field] = dc_field(default_factory=set)


class Outcome(StrEnum):
    PROMPT = "prompt"
    RETRY = "retry"
    CONFIRM = "confirm"
    DONE = "done"
    AWAITING_EVIDENCE = "awaiting_evidence"
    EVIDENCE_FULL = "evidence_full"
    CANCELLED = "cancelled"
    RESTARTED = "restarted"


@dataclass(frozen=True)
class Step:
    outcome: Outcome
    field: Field | None = None
    retry_count: int = 0


class InvalidState(RuntimeError):
    """Transición no permitida desde el estado actual."""


def _is_missing(draft: ReportDraft, field: Field) -> bool:
    if field is Field.EVIDENCE:
        return draft.evidence_count == 0 and not draft.evidence_skipped
    if field in draft.omitted:
        return False
    value = getattr(draft, field.value)
    if field is Field.DESCRIPTION:
        return not (value or "").strip()
    return value is None


def _parse_bool(raw: str) -> bool:
    key = normalize(raw)
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    raise ValueError(f"valor booleano desconocido: {raw!r}")


def _parse_value(field: Field, raw: str) -> object:
    if field is Field.THREAT_TYPE:
        return resolve_threat_type(raw)
    if field is Field.SEVERITY:
        return resolve_severity(raw)
    if field is Field.PROVINCE:
        return resolve_province(raw)
    if field is Field.ATTACK_MEDIUM:
        return resolve_attack_medium(raw)
    if field is Field.HAD_ECONOMIC_LOSS:
        return _parse_bool(raw)
    if field in (Field.LOCALITY, Field.DESCRIPTION):
        return raw
    raise ValueError(f"campo sin respuesta de texto: {field!r}")


class Survey:
    def __init__(self) -> None:
        self.state = ConversationState.AWAITING_FIRST
        self.draft = ReportDraft()
        self.current_field: Field | None = None
        self.retry_count = 0

    @classmethod
    def restore(
        cls,
        *,
        state: ConversationState,
        draft: ReportDraft,
        current_field: Field | None = None,
        retry_count: int = 0,
    ) -> "Survey":
        survey = cls()
        survey.state = state
        survey.draft = draft
        survey.current_field = current_field
        survey.retry_count = retry_count
        return survey

    def begin(self, draft: ReportDraft | None = None) -> Step:
        if self.state is not ConversationState.AWAITING_FIRST:
            raise InvalidState(f"no se puede comenzar desde {self.state}")
        if draft is not None:
            self.draft = draft
        self.state = ConversationState.COLLECTING
        self.retry_count = 0
        return self._advance()

    def submit(self, text: str) -> Step:
        if self.state is ConversationState.CONFIRMING:
            return self._submit_confirmation(text)
        if self.state is not ConversationState.COLLECTING or self.current_field is None:
            raise InvalidState(f"no se espera una respuesta en {self.state}")
        return self._submit_answer(self.current_field, text)

    def add_evidence(self) -> Step:
        if self.state not in (ConversationState.COLLECTING, ConversationState.CONFIRMING):
            raise InvalidState(f"no se puede adjuntar evidencia en {self.state}")
        if self.draft.evidence_count >= MAX_EVIDENCE:
            return Step(Outcome.EVIDENCE_FULL, Field.EVIDENCE, self.retry_count)
        self.draft.evidence_count += 1
        if self.current_field is Field.EVIDENCE:
            self.draft.evidence_skipped = False
            self.retry_count = 0
            return self._advance()
        if self.state is ConversationState.CONFIRMING:
            return Step(Outcome.CONFIRM)
        return Step(Outcome.PROMPT, self.current_field, self.retry_count)

    def correct(self, field: Field) -> Step:
        if self.state is not ConversationState.CONFIRMING:
            raise InvalidState(f"no se puede corregir en {self.state}")
        return self._start_correction(field)

    def restart(self) -> Step:
        self._reset()
        return Step(Outcome.RESTARTED)

    def cancel(self) -> Step:
        self._reset()
        return Step(Outcome.CANCELLED)

    def summary(self) -> list[tuple[str, str]]:
        return [(FIELD_LABELS[field], _display(self.draft, field)) for field in FIELD_ORDER]

    def _submit_answer(self, field: Field, text: str) -> Step:
        raw = text.strip()
        if not raw:
            return self._retry()
        if field is Field.EVIDENCE:
            return self._submit_evidence(raw)
        if field in OPTIONAL_FIELDS and normalize(raw) == "omitir":
            setattr(self.draft, field.value, None)
            self.draft.omitted.add(field)
            self.retry_count = 0
            return self._advance()
        try:
            value = _parse_value(field, raw)
        except (CatalogError, ValueError):
            return self._retry()
        setattr(self.draft, field.value, value)
        self.draft.omitted.discard(field)
        self.retry_count = 0
        return self._advance()

    def _submit_evidence(self, raw: str) -> Step:
        key = normalize(raw)
        if key in _EVIDENCE_YES:
            return Step(Outcome.AWAITING_EVIDENCE, Field.EVIDENCE, self.retry_count)
        if key in _EVIDENCE_NO:
            self.draft.evidence_skipped = True
            self.retry_count = 0
            return self._advance()
        return self._retry()

    def _submit_confirmation(self, text: str) -> Step:
        key = normalize(text)
        if key in {"confirmar", "confirmo", "si"}:
            self.state = ConversationState.DONE
            self.current_field = None
            return Step(Outcome.DONE)
        if key.startswith("corregir "):
            try:
                field = resolve_field(key.removeprefix("corregir "))
            except CatalogError:
                return self._retry()
            return self._start_correction(field)
        return self._retry()

    def _start_correction(self, field: Field) -> Step:
        setattr(self.draft, field.value, None)
        self.draft.omitted.discard(field)
        if field is Field.EVIDENCE:
            self.draft.evidence_count = 0
            self.draft.evidence_skipped = False
        self.state = ConversationState.COLLECTING
        self.current_field = field
        self.retry_count = 0
        return Step(Outcome.PROMPT, field, 0)

    def _advance(self) -> Step:
        for field in FIELD_ORDER:
            if _is_missing(self.draft, field):
                self.current_field = field
                return Step(Outcome.PROMPT, field, self.retry_count)
        self.current_field = None
        self.state = ConversationState.CONFIRMING
        return Step(Outcome.CONFIRM, None, 0)

    def _retry(self) -> Step:
        self.retry_count += 1
        return Step(Outcome.RETRY, self.current_field, self.retry_count)

    def _reset(self) -> None:
        self.state = ConversationState.AWAITING_FIRST
        self.draft = ReportDraft()
        self.current_field = None
        self.retry_count = 0


def _display(draft: ReportDraft, field: Field) -> str:
    if field is Field.EVIDENCE:
        if draft.evidence_count == 0:
            return "Sin evidencia"
        return "1 archivo" if draft.evidence_count == 1 else f"{draft.evidence_count} archivos"
    value = getattr(draft, field.value)
    if value is None:
        return "Sin informar"
    if field is Field.HAD_ECONOMIC_LOSS:
        return "Sí" if value else "No"
    if isinstance(value, (ThreatType, Severity, Province, AttackMedium)):
        return value.label
    return str(value)
