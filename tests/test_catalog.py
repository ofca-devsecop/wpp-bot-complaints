import pytest

from sicar_bot.domain.catalog import (
    AmbiguousValue,
    AttackMedium,
    Province,
    ReportStatus,
    Severity,
    ThreatType,
    UnknownValue,
    match_option,
    normalize,
    resolve_attack_medium,
    resolve_province,
    resolve_severity,
    resolve_threat_type,
)

PROVINCE_ALIASES = [
    ("CABA", Province.CABA),
    ("Capital Federal", Province.CABA),
    ("Ciudad de Buenos Aires", Province.CABA),
    ("Bs As", Province.BUENOS_AIRES),
    ("Baires", Province.BUENOS_AIRES),
    ("Tucuman", Province.TUCUMAN),
    ("Cordoba", Province.CORDOBA),
    ("Entre Rios", Province.ENTRE_RIOS),
    ("Rio Negro", Province.RIO_NEGRO),
    ("Neuquen", Province.NEUQUEN),
]

INVALID_VALUES = ["", "Atlántida", "Provincia de Marte", "12345", "buenos"]


def test_there_are_24_provinces() -> None:
    assert len(Province) == 24


@pytest.mark.parametrize("province", list(Province))
def test_province_label_and_value_resolve(province: Province) -> None:
    assert resolve_province(province.label) is province
    assert resolve_province(province.value) is province


@pytest.mark.parametrize(("alias", "expected"), PROVINCE_ALIASES)
def test_province_alias_resolves(alias: str, expected: Province) -> None:
    assert resolve_province(alias) is expected


@pytest.mark.parametrize("raw", INVALID_VALUES)
def test_unknown_province_is_rejected(raw: str) -> None:
    with pytest.raises(UnknownValue):
        resolve_province(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C.A.B.A.", Province.CABA),
        ("  Córdoba  ", Province.CORDOBA),
        ("BS.AS.", Province.BUENOS_AIRES),
    ],
)
def test_normalization_ignores_case_accents_and_punctuation(raw: str, expected: Province) -> None:
    assert resolve_province(raw) is expected


def test_ambiguous_alias_is_rejected() -> None:
    index = {"santa": frozenset({Province.SANTA_FE, Province.SANTA_CRUZ})}

    with pytest.raises(AmbiguousValue):
        match_option(index, "Santa")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fraude en línea", ThreatType.FRAUDE_EN_LINEA),
        ("acceso_ilegitimo", ThreatType.ACCESO_ILEGITIMO),
        ("PHISHING", ThreatType.PHISHING),
    ],
)
def test_threat_type_resolves(raw: str, expected: ThreatType) -> None:
    assert resolve_threat_type(raw) is expected


@pytest.mark.parametrize(("raw", "expected"), [("ALTA", Severity.ALTA), ("Media", Severity.MEDIA)])
def test_severity_resolves(raw: str, expected: Severity) -> None:
    assert resolve_severity(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Sitio web falso", AttackMedium.SITIO_FALSO), ("whatsapp", AttackMedium.WHATSAPP)],
)
def test_attack_medium_resolves(raw: str, expected: AttackMedium) -> None:
    assert resolve_attack_medium(raw) is expected


@pytest.mark.parametrize("enum_cls", [ThreatType, Severity, AttackMedium, ReportStatus, Province])
def test_every_member_has_a_label(enum_cls: type) -> None:
    assert all(member.label for member in enum_cls)


def test_normalize_collapses_whitespace() -> None:
    assert normalize("  Río   Negro ") == "rio negro"
