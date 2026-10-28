"""Textos legales y conversacionales. Cambiarlos no toca la lógica."""

from sicar_bot.domain.catalog import AttackMedium, Province, Severity, ThreatType
from sicar_bot.domain.survey import Field, Survey

WELCOME = """Hola. Soy el asistente de SICAR-C para recibir denuncias de ciberdelitos.
Contame qué pasó con tus palabras y, si tenés, mandá capturas o comprobantes.
Te voy a hacer algunas preguntas para completar el registro.

Este registro no reemplaza una denuncia formal. Si estás en peligro, llamá al 911.

Al continuar aceptás el aviso de privacidad: tus datos se usan solo para
registrar y derivar tu denuncia a SICAR-C. Guardamos tu teléfono, el relato
y las imágenes que envíes. No se usan para publicidad. Responsable: OFCA.
Ley 25.326."""

EVIDENCE_REQUEST = """Si tenés capturas de la conversación, el comprobante de la transferencia o el
perfil del atacante, mandalas ahora. No compartas datos que no quieras registrar."""

CLOSING = """Listo. Tu denuncia quedó registrada con el código {codigo}. Guardalo.
Un operador de SICAR-C la va a revisar.

Este registro no reemplaza una denuncia formal ante la justicia. Para denuncias
penales, acercate a la fiscalía o comisaría más cercana. Ante una emergencia,
llamá al 911."""

EVIDENCE_FULL = (
    "Ya guardé el máximo de 5 archivos para esta denuncia. Escribí NO para seguir con el registro."
)

EVIDENCE_REJECTED = (
    "No pude guardar ese archivo. Acepto fotos (JPG, PNG, WEBP) o PDF de hasta 10 MB. "
    "Probá de nuevo o escribí NO para seguir sin evidencia."
)

ALREADY_REGISTERED = """Tu denuncia ya quedó registrada. Si querés hacer otra, escribí HOLA."""

HELP = """Podés escribirme lo que pasó y te guío con preguntas.
Comandos: ESTADO (ver cómo va), CANCELAR (descartar el borrador),
REINICIAR (empezar de nuevo), AYUDA (este mensaje)."""

STATUS_EMPTY = """Todavía no tenés una denuncia en curso. Contame qué pasó y la empezamos."""

STATUS_PROGRESS = """Tu denuncia está en borrador. Te falta: {campo}.
Escribí CANCELAR para descartarla."""

STATUS_SUBMITTED = """Tu denuncia está registrada con el código {codigo}."""

CANCELLED = """Cancelé el registro y borré los datos del borrador. Si querés empezar de nuevo,
escribí HOLA."""

FIELD_OPTIONS: dict[Field, tuple[str, ...]] = {
    Field.THREAT_TYPE: tuple(item.label for item in ThreatType),
    Field.SEVERITY: tuple(item.label for item in Severity),
    Field.PROVINCE: tuple(item.label for item in Province),
    Field.ATTACK_MEDIUM: tuple(item.label for item in AttackMedium),
    Field.HAD_ECONOMIC_LOSS: ("Sí", "No"),
}

_PROMPTS: dict[Field, str] = {
    Field.THREAT_TYPE: "¿Qué tipo de amenaza fue?",
    Field.SEVERITY: "¿Qué tan grave lo considerás?",
    Field.PROVINCE: "¿En qué provincia ocurrió? Escribí el nombre (por ejemplo: Córdoba, CABA).",
    Field.LOCALITY: "¿En qué localidad pasó? Si preferís no decirlo, escribí OMITIR.",
    Field.ATTACK_MEDIUM: "¿Por dónde te contactó o atacó?",
    Field.HAD_ECONOMIC_LOSS: "¿Tuviste una pérdida de dinero?",
    Field.DESCRIPTION: "Contame brevemente qué pasó.",
    Field.EVIDENCE: (
        "¿Tenés capturas o comprobantes para adjuntar? "
        "Escribí SÍ y mandalos, o NO para seguir sin evidencia."
    ),
}

_PROVINCE_RETRY = "¿En qué provincia ocurrió? Elegí una opción."


def _numbered(labels: tuple[str, ...]) -> str:
    return "\n".join(f"{index}. {label}" for index, label in enumerate(labels, 1))


def question(field: Field, *, retry: bool = False) -> str:
    """Texto de la pregunta. En el reintento se listan siempre las opciones."""
    options = FIELD_OPTIONS.get(field)
    prompt = _PROVINCE_RETRY if retry and field is Field.PROVINCE else _PROMPTS[field]
    if options is not None and (field is not Field.PROVINCE or retry):
        prompt = f"{prompt}\n{_numbered(options)}"
    return f"No entendí. {prompt}" if retry else prompt


def choice(field: Field, raw: str) -> str | None:
    """Traduce un número de opción a la etiqueta que entiende el catálogo."""
    options = FIELD_OPTIONS.get(field)
    text = raw.strip()
    if options is None or not text.isdigit():
        return None
    index = int(text)
    if 1 <= index <= len(options):
        return options[index - 1]
    return None


def summary(survey: Survey) -> str:
    lines = [
        f"{index}. {label}: {value}" for index, (label, value) in enumerate(survey.summary(), 1)
    ]
    return (
        "Resumen de tu denuncia:\n"
        + "\n".join(lines)
        + "\n\nSi está bien, escribí CONFIRMAR. Si algo está mal, escribí CORREGIR <campo>."
    )
