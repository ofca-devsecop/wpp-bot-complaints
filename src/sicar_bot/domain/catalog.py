import unicodedata
from collections.abc import Iterable, Mapping
from enum import StrEnum


class CatalogError(ValueError):
    """Valor que no pertenece a un catálogo."""


class UnknownValue(CatalogError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"valor desconocido: {raw!r}")
        self.raw = raw


class AmbiguousValue(CatalogError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"valor ambiguo: {raw!r}")
        self.raw = raw


def normalize(text: str) -> str:
    """Minúsculas, sin acentos ni puntuación, espacios colapsados."""
    decomposed = unicodedata.normalize("NFKD", text)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = "".join(char for char in unaccented if char.isalnum() or char.isspace())
    return " ".join(cleaned.lower().split())


class LabeledStrEnum(StrEnum):
    @property
    def label(self) -> str:
        return _LABELS[type(self)][self.value]


class ThreatType(LabeledStrEnum):
    FRAUDE_EN_LINEA = "fraude_en_linea"
    USURPACION_DE_IDENTIDAD = "usurpacion_de_identidad"
    ACCESO_ILEGITIMO = "acceso_ilegitimo"
    PHISHING = "phishing"
    ACOSO = "acoso"
    OTROS = "otros"


class Severity(LabeledStrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class AttackMedium(LabeledStrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    LLAMADA = "llamada"
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    CORREO = "correo"
    SITIO_FALSO = "sitio_falso"
    OTRO = "otro"


class ReportStatus(LabeledStrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    EXPORTED = "exported"
    ARCHIVED = "archived"
    ABANDONED = "abandoned"


class Province(LabeledStrEnum):
    BUENOS_AIRES = "buenos_aires"
    CATAMARCA = "catamarca"
    CHACO = "chaco"
    CHUBUT = "chubut"
    CABA = "caba"
    CORDOBA = "cordoba"
    CORRIENTES = "corrientes"
    ENTRE_RIOS = "entre_rios"
    FORMOSA = "formosa"
    JUJUY = "jujuy"
    LA_PAMPA = "la_pampa"
    LA_RIOJA = "la_rioja"
    MENDOZA = "mendoza"
    MISIONES = "misiones"
    NEUQUEN = "neuquen"
    RIO_NEGRO = "rio_negro"
    SALTA = "salta"
    SAN_JUAN = "san_juan"
    SAN_LUIS = "san_luis"
    SANTA_CRUZ = "santa_cruz"
    SANTA_FE = "santa_fe"
    SANTIAGO_DEL_ESTERO = "santiago_del_estero"
    TIERRA_DEL_FUEGO = "tierra_del_fuego"
    TUCUMAN = "tucuman"


_LABELS: dict[type[LabeledStrEnum], dict[str, str]] = {
    ThreatType: {
        "fraude_en_linea": "Fraude en línea",
        "usurpacion_de_identidad": "Usurpación de identidad",
        "acceso_ilegitimo": "Acceso ilegítimo",
        "phishing": "Phishing",
        "acoso": "Acoso",
        "otros": "Otros",
    },
    Severity: {"alta": "Alta", "media": "Media", "baja": "Baja"},
    AttackMedium: {
        "whatsapp": "WhatsApp",
        "sms": "SMS",
        "llamada": "Llamada telefónica",
        "telegram": "Telegram",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "correo": "Correo electrónico",
        "sitio_falso": "Sitio web falso",
        "otro": "Otro",
    },
    ReportStatus: {
        "draft": "Borrador",
        "submitted": "Enviada",
        "exported": "Exportada",
        "archived": "Archivada",
        "abandoned": "Abandonada",
    },
    Province: {
        "buenos_aires": "Buenos Aires",
        "catamarca": "Catamarca",
        "chaco": "Chaco",
        "chubut": "Chubut",
        "caba": "Ciudad Autónoma de Buenos Aires",
        "cordoba": "Córdoba",
        "corrientes": "Corrientes",
        "entre_rios": "Entre Ríos",
        "formosa": "Formosa",
        "jujuy": "Jujuy",
        "la_pampa": "La Pampa",
        "la_rioja": "La Rioja",
        "mendoza": "Mendoza",
        "misiones": "Misiones",
        "neuquen": "Neuquén",
        "rio_negro": "Río Negro",
        "salta": "Salta",
        "san_juan": "San Juan",
        "san_luis": "San Luis",
        "santa_cruz": "Santa Cruz",
        "santa_fe": "Santa Fe",
        "santiago_del_estero": "Santiago del Estero",
        "tierra_del_fuego": "Tierra del Fuego",
        "tucuman": "Tucumán",
    },
}

PROVINCE_ALIASES: dict[str, Province] = {
    "caba": Province.CABA,
    "capital federal": Province.CABA,
    "ciudad de buenos aires": Province.CABA,
    "bs as": Province.BUENOS_AIRES,
    "bsas": Province.BUENOS_AIRES,
    "baires": Province.BUENOS_AIRES,
}


def build_index[T](entries: Iterable[tuple[str, T]]) -> dict[str, frozenset[T]]:
    index: dict[str, set[T]] = {}
    for text, value in entries:
        index.setdefault(normalize(text), set()).add(value)
    return {key: frozenset(values) for key, values in index.items()}


def _enum_index[E: LabeledStrEnum](enum_cls: type[E]) -> dict[str, frozenset[E]]:
    return build_index(
        [(member.value, member) for member in enum_cls]
        + [(member.label, member) for member in enum_cls]
    )


def match_option[T](index: Mapping[str, frozenset[T]], raw: str) -> T:
    values = index.get(normalize(raw), frozenset())
    if not values:
        raise UnknownValue(raw)
    if len(values) > 1:
        raise AmbiguousValue(raw)
    return next(iter(values))


_PROVINCE_INDEX = build_index(
    [(province.label, province) for province in Province]
    + [(province.value, province) for province in Province]
    + list(PROVINCE_ALIASES.items())
)
_THREAT_INDEX = _enum_index(ThreatType)
_SEVERITY_INDEX = _enum_index(Severity)
_MEDIUM_INDEX = _enum_index(AttackMedium)


def resolve_province(raw: str) -> Province:
    return match_option(_PROVINCE_INDEX, raw)


def resolve_threat_type(raw: str) -> ThreatType:
    return match_option(_THREAT_INDEX, raw)


def resolve_severity(raw: str) -> Severity:
    return match_option(_SEVERITY_INDEX, raw)


def resolve_attack_medium(raw: str) -> AttackMedium:
    return match_option(_MEDIUM_INDEX, raw)
