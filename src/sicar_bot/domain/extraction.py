"""Contrato de extracción LLM: modelos de cable, dominio y prellenado."""

from dataclasses import dataclass

from pydantic import BaseModel

from sicar_bot.domain.catalog import (
    AttackMedium,
    CatalogError,
    Province,
    Severity,
    ThreatType,
    resolve_attack_medium,
    resolve_province,
    resolve_severity,
    resolve_threat_type,
)
from sicar_bot.domain.survey import Field, ReportDraft

DEFAULT_CONFIDENCE_THRESHOLD = 0.7


class WireGuess(BaseModel):
    """Forma JSON que devuelve el LLM. `value` vacío significa desconocido."""

    value: str = ""
    confidence: float = 0.0


class WireExtraction(BaseModel):
    threat_type: WireGuess = WireGuess()
    severity: WireGuess = WireGuess()
    province: WireGuess = WireGuess()
    locality: WireGuess = WireGuess()
    attack_medium: WireGuess = WireGuess()
    had_economic_loss: WireGuess = WireGuess()
    description: WireGuess = WireGuess()
    evidence_summary: str = ""


class WireReceipt(BaseModel):
    """Datos visibles en un comprobante. `""` significa que no se pudo leer."""

    amount: str = ""
    currency: str = ""
    date: str = ""
    bank: str = ""
    account: str = ""
    operation_number: str = ""
    summary: str = ""


@dataclass(frozen=True)
class Guess[T]:
    value: T | None
    confidence: float


@dataclass(frozen=True)
class Extraction:
    threat_type: Guess[ThreatType]
    severity: Guess[Severity]
    province: Guess[Province]
    locality: Guess[str]
    attack_medium: Guess[AttackMedium]
    had_economic_loss: Guess[bool]
    description: Guess[str]
    evidence_summary: str | None = None


@dataclass(frozen=True)
class ReceiptExtraction:
    amount: str | None = None
    currency: str | None = None
    date: str | None = None
    bank: str | None = None
    account: str | None = None
    operation_number: str | None = None
    summary: str | None = None


_TRUE = frozenset({"si", "s", "true", "1", "yes"})
_FALSE = frozenset({"no", "n", "false", "0"})


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _guess(raw: WireGuess, resolve: object) -> Guess[object]:
    text = raw.value.strip()
    if not text:
        return Guess(None, 0.0)
    try:
        return Guess(resolve(text), _clamp(raw.confidence))  # type: ignore[operator]
    except (CatalogError, ValueError):
        return Guess(None, 0.0)


def _guess_text(raw: WireGuess) -> Guess[str]:
    text = raw.value.strip()
    if not text:
        return Guess(None, 0.0)
    return Guess(text, _clamp(raw.confidence))


def _guess_bool(raw: WireGuess) -> Guess[bool]:
    key = raw.value.strip().lower()
    if key in _TRUE:
        return Guess(True, _clamp(raw.confidence))
    if key in _FALSE:
        return Guess(False, _clamp(raw.confidence))
    return Guess(None, 0.0)


def to_extraction(wire: WireExtraction) -> Extraction:
    summary = wire.evidence_summary.strip()
    return Extraction(
        threat_type=_guess(wire.threat_type, resolve_threat_type),
        severity=_guess(wire.severity, resolve_severity),
        province=_guess(wire.province, resolve_province),
        locality=_guess_text(wire.locality),
        attack_medium=_guess(wire.attack_medium, resolve_attack_medium),
        had_economic_loss=_guess_bool(wire.had_economic_loss),
        description=_guess_text(wire.description),
        evidence_summary=summary or None,
    )


def _text_or_none(value: str) -> str | None:
    text = value.strip()
    return text or None


def to_receipt(wire: WireReceipt) -> ReceiptExtraction:
    return ReceiptExtraction(
        amount=_text_or_none(wire.amount),
        currency=_text_or_none(wire.currency),
        date=_text_or_none(wire.date),
        bank=_text_or_none(wire.bank),
        account=_text_or_none(wire.account),
        operation_number=_text_or_none(wire.operation_number),
        summary=_text_or_none(wire.summary),
    )


def _guesses(extraction: Extraction) -> tuple[tuple[Field, Guess[object]], ...]:
    return (
        (Field.THREAT_TYPE, extraction.threat_type),
        (Field.SEVERITY, extraction.severity),
        (Field.PROVINCE, extraction.province),
        (Field.LOCALITY, extraction.locality),
        (Field.ATTACK_MEDIUM, extraction.attack_medium),
        (Field.HAD_ECONOMIC_LOSS, extraction.had_economic_loss),
        (Field.DESCRIPTION, extraction.description),
    )


def prefill(
    draft: ReportDraft,
    extraction: Extraction,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ReportDraft:
    for field, guess in _guesses(extraction):
        if guess.value is None or guess.confidence < threshold:
            continue
        if field in draft.omitted:
            continue
        if getattr(draft, field.value) is not None:
            continue
        setattr(draft, field.value, guess.value)
    return draft
