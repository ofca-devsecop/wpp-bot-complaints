from sicar_bot.domain.catalog import AttackMedium, Province, Severity, ThreatType
from sicar_bot.domain.extraction import (
    Extraction,
    Guess,
    WireExtraction,
    prefill,
    to_extraction,
)
from sicar_bot.domain.survey import Field, ReportDraft

EMPTY = Guess(None, 0.0)


def extraction(**overrides: Guess) -> Extraction:
    values = {
        "threat_type": EMPTY,
        "severity": EMPTY,
        "province": EMPTY,
        "locality": EMPTY,
        "attack_medium": EMPTY,
        "had_economic_loss": EMPTY,
        "description": EMPTY,
        "evidence_summary": None,
    }
    values.update(overrides)
    return Extraction(**values)


def test_valid_values_map_to_catalog_enums() -> None:
    wire = WireExtraction(
        threat_type={"value": "phishing", "confidence": 0.9},
        severity={"value": "Alta", "confidence": 0.8},
        province={"value": "Bs As", "confidence": 0.7},
        attack_medium={"value": "whatsapp", "confidence": 0.95},
        had_economic_loss={"value": "si", "confidence": 0.8},
        description={"value": "  me pidieron el código  ", "confidence": 0.9},
    )

    result = to_extraction(wire)

    assert result.threat_type.value is ThreatType.PHISHING
    assert result.severity.value is Severity.ALTA
    assert result.province.value is Province.BUENOS_AIRES
    assert result.attack_medium.value is AttackMedium.WHATSAPP
    assert result.had_economic_loss.value is True
    assert result.description.value == "me pidieron el código"


def test_unknown_enum_becomes_null() -> None:
    wire = WireExtraction(
        threat_type={"value": "inventado", "confidence": 0.99},
        province={"value": "Marte", "confidence": 0.99},
        had_economic_loss={"value": "tal vez", "confidence": 0.99},
    )

    result = to_extraction(wire)

    assert result.threat_type == EMPTY
    assert result.province == EMPTY
    assert result.had_economic_loss == EMPTY


def test_empty_values_stay_null() -> None:
    result = to_extraction(WireExtraction())

    assert result.threat_type == EMPTY
    assert result.description == EMPTY
    assert result.evidence_summary is None


def test_confidence_is_clamped() -> None:
    wire = WireExtraction(severity={"value": "media", "confidence": 5.0})
    assert to_extraction(wire).severity.confidence == 1.0


def test_prefill_applies_threshold_and_only_missing_fields() -> None:
    draft = ReportDraft(severity=Severity.BAJA, omitted={Field.LOCALITY})
    guess = extraction(
        threat_type=Guess(ThreatType.PHISHING, 0.9),
        severity=Guess(Severity.ALTA, 0.95),
        province=Guess(Province.CORDOBA, 0.7),
        locality=Guess("Villa Carlos Paz", 0.9),
        attack_medium=Guess(AttackMedium.SMS, 0.69),
        had_economic_loss=Guess(True, 0.8),
        description=Guess("me estafaron", 0.75),
    )

    prefill(draft, guess)

    assert draft.threat_type is ThreatType.PHISHING
    assert draft.severity is Severity.BAJA
    assert draft.province is Province.CORDOBA
    assert draft.locality is None
    assert draft.attack_medium is None
    assert draft.had_economic_loss is True
    assert draft.description == "me estafaron"
