import pytest

from sicar_bot.domain.catalog import AttackMedium, Province, Severity, ThreatType
from sicar_bot.domain.survey import (
    MAX_EVIDENCE,
    Field,
    InvalidState,
    Outcome,
    ReportDraft,
    Survey,
    resolve_field,
)


def complete() -> ReportDraft:
    return ReportDraft(
        threat_type=ThreatType.PHISHING,
        severity=Severity.ALTA,
        province=Province.CORDOBA,
        locality="Villa Carlos Paz",
        attack_medium=AttackMedium.CORREO,
        had_economic_loss=True,
        description="Me pidieron la clave del home banking por correo.",
        evidence_count=1,
    )


ANSWERS = {
    Field.THREAT_TYPE: "Phishing",
    Field.SEVERITY: "Alta",
    Field.PROVINCE: "Córdoba",
    Field.LOCALITY: "Villa Carlos Paz",
    Field.ATTACK_MEDIUM: "Correo electrónico",
    Field.HAD_ECONOMIC_LOSS: "Sí",
    Field.DESCRIPTION: "Me pidieron la clave del home banking por correo.",
}


def started(draft: ReportDraft | None = None) -> Survey:
    survey = Survey()
    survey.begin(draft)
    return survey


def test_empty_draft_starts_with_threat_type() -> None:
    survey = started()

    assert survey.state == "collecting"
    assert survey.current_field is Field.THREAT_TYPE


def test_fields_are_asked_in_order_until_confirmation() -> None:
    survey = started()
    seen: list[Field] = []

    for _ in range(len(ANSWERS)):
        assert survey.current_field is not None
        seen.append(survey.current_field)
        step = survey.submit(ANSWERS[survey.current_field])
        assert step.outcome is Outcome.PROMPT

    assert seen == list(ANSWERS)
    assert step.field is Field.EVIDENCE
    assert survey.submit("no").outcome is Outcome.CONFIRM
    assert survey.state == "confirming"


def test_prefilled_draft_skips_known_fields() -> None:
    survey = started(complete())

    assert survey.state == "confirming"
    assert survey.current_field is None


def test_invalid_answer_retries_and_keeps_field() -> None:
    survey = started()

    step = survey.submit("no existe")

    assert step.outcome is Outcome.RETRY
    assert step.field is Field.THREAT_TYPE
    assert step.retry_count == 1
    assert survey.current_field is Field.THREAT_TYPE


def test_second_invalid_answer_keeps_retrying() -> None:
    survey = started()
    survey.submit("no existe")

    step = survey.submit("tampoco")

    assert step.outcome is Outcome.RETRY
    assert step.retry_count == 2


def test_valid_answer_after_retry_resets_counter() -> None:
    survey = started()
    survey.submit("no existe")

    step = survey.submit("Phishing")

    assert step.outcome is Outcome.PROMPT
    assert step.field is Field.SEVERITY
    assert step.retry_count == 0


def test_omitir_is_rejected_on_required_field() -> None:
    survey = started()

    assert survey.submit("omitir").outcome is Outcome.RETRY


def test_omitir_advances_optional_field() -> None:
    survey = started(ReportDraft(threat_type=ThreatType.ACOSO, severity=Severity.BAJA))
    assert survey.current_field is Field.PROVINCE

    survey.submit("Salta")
    assert survey.current_field is Field.LOCALITY

    step = survey.submit("omitir")

    assert step.field is Field.ATTACK_MEDIUM
    assert survey.draft.locality is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("sí", True), ("SI", True), ("No", False), ("nunca", False)],
)
def test_economic_loss_accepts_yes_no(raw: str, expected: bool) -> None:
    survey = started(
        ReportDraft(
            threat_type=ThreatType.FRAUDE_EN_LINEA,
            severity=Severity.MEDIA,
            province=Province.MENDOZA,
            locality="Godoy Cruz",
            attack_medium=AttackMedium.WHATSAPP,
        )
    )

    step = survey.submit(raw)

    assert survey.draft.had_economic_loss is expected
    assert step.field is Field.DESCRIPTION


def test_economic_loss_rejects_unknown_answer() -> None:
    survey = started(
        ReportDraft(
            threat_type=ThreatType.ACOSO,
            severity=Severity.BAJA,
            province=Province.SALTA,
            locality="Orán",
            attack_medium=AttackMedium.INSTAGRAM,
        )
    )
    assert survey.current_field is Field.HAD_ECONOMIC_LOSS

    assert survey.submit("tal vez").outcome is Outcome.RETRY


def test_prefilled_description_jumps_to_evidence() -> None:
    draft = ReportDraft(
        threat_type=ThreatType.ACOSO,
        severity=Severity.BAJA,
        province=Province.SALTA,
        locality="Orán",
        attack_medium=AttackMedium.INSTAGRAM,
        had_economic_loss=False,
        description="Me escriben desde cuentas falsas.",
    )

    survey = started(draft)

    assert survey.current_field is Field.EVIDENCE


def test_evidence_yes_waits_and_advances_when_file_arrives() -> None:
    survey = started(complete())

    survey.correct(Field.EVIDENCE)
    step = survey.submit("sí")

    assert step.outcome is Outcome.AWAITING_EVIDENCE
    assert survey.current_field is Field.EVIDENCE

    step = survey.add_evidence()

    assert step.outcome is Outcome.CONFIRM
    assert survey.draft.evidence_count == 1


def test_evidence_arriving_while_confirming_is_accepted() -> None:
    survey = started(complete())

    step = survey.add_evidence()

    assert step.outcome is Outcome.CONFIRM
    assert survey.draft.evidence_count == 2


def test_evidence_beyond_limit_is_rejected() -> None:
    draft = complete()
    draft.evidence_count = MAX_EVIDENCE
    survey = started(draft)

    step = survey.add_evidence()

    assert step.outcome is Outcome.EVIDENCE_FULL
    assert survey.draft.evidence_count == MAX_EVIDENCE


def test_evidence_no_skips_to_confirmation() -> None:
    draft = ReportDraft(
        threat_type=ThreatType.ACOSO,
        severity=Severity.BAJA,
        province=Province.SALTA,
        locality="Orán",
        attack_medium=AttackMedium.INSTAGRAM,
        had_economic_loss=False,
        description="Me escriben desde cuentas falsas.",
    )
    survey = started(draft)

    step = survey.submit("omitir")

    assert step.outcome is Outcome.CONFIRM
    assert survey.draft.evidence_skipped is True


def test_confirmar_finishes_survey() -> None:
    survey = started(complete())

    step = survey.submit("confirmar")

    assert step.outcome is Outcome.DONE
    assert survey.state == "done"
    with pytest.raises(InvalidState):
        survey.submit("otra cosa")


def test_corregir_reopens_one_field_and_replaces_value() -> None:
    survey = started(complete())

    step = survey.submit("corregir provincia")

    assert step.outcome is Outcome.PROMPT
    assert step.field is Field.PROVINCE
    assert survey.state == "collecting"
    assert survey.draft.province is None

    survey.submit("Salta")

    assert survey.draft.province is Province.SALTA
    assert survey.state == "confirming"


def test_corregir_unknown_field_retries() -> None:
    survey = started(complete())

    step = survey.submit("corregir color favorito")

    assert step.outcome is Outcome.RETRY
    assert survey.state == "confirming"


def test_confirm_with_unknown_answer_retries() -> None:
    survey = started(complete())

    step = survey.submit("quizás")

    assert step.outcome is Outcome.RETRY
    assert survey.state == "confirming"


def test_restart_clears_draft_and_returns_to_start() -> None:
    survey = started(complete())

    step = survey.restart()

    assert step.outcome is Outcome.RESTARTED
    assert survey.state == "awaiting_first"
    assert survey.draft == ReportDraft()


def test_cancel_clears_draft() -> None:
    survey = started(complete())

    step = survey.cancel()

    assert step.outcome is Outcome.CANCELLED
    assert survey.state == "awaiting_first"
    assert survey.draft == ReportDraft()


def test_begin_twice_is_rejected() -> None:
    survey = started()

    with pytest.raises(InvalidState):
        survey.begin()


def test_submit_before_begin_is_rejected() -> None:
    with pytest.raises(InvalidState):
        Survey().submit("hola")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Provincia", Field.PROVINCE), ("perdida economica", Field.HAD_ECONOMIC_LOSS)],
)
def test_resolve_field_accepts_alias(raw: str, expected: Field) -> None:
    assert resolve_field(raw) is expected


def test_summary_uses_labels_and_spanish_values() -> None:
    summary = dict(started(complete()).summary())

    assert summary["Tipo de amenaza"] == "Phishing"
    assert summary["Provincia"] == "Córdoba"
    assert summary["Pérdida económica"] == "Sí"
    assert summary["Evidencia"] == "1 archivo"


def test_summary_shows_missing_optionals() -> None:
    survey = started(
        ReportDraft(
            threat_type=ThreatType.OTROS,
            severity=Severity.BAJA,
            province=Province.FORMOSA,
            description="Hecho sin más datos.",
        )
    )
    summary = dict(survey.summary())

    assert summary["Localidad"] == "Sin informar"
    assert summary["Medio del atacante"] == "Sin informar"
    assert summary["Pérdida económica"] == "Sin informar"
    assert summary["Evidencia"] == "Sin evidencia"
