"""Redacción de datos personales antes de enviar texto a un LLM externo."""

import re

CBU_RE = re.compile(r"(?<!\d)(?:\d[\s-]?){21}\d(?!\d)")
PHONE_RE = re.compile(
    r"(?<![\d+])(?:\+?54[\s.-]?9?[\s.-]?)?(?:11|\d{2,4})[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"
)
ALIAS_RE = re.compile(r"(?i:(alias|a la cuenta))\s*:?\s*([a-z0-9]+(?:\.[a-z0-9]+){1,})")
NAME_RE = re.compile(
    r"(?i:(me llamo|mi nombre es|soy))\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,3})"
)

_TOKEN = {
    "cbu": "[CBU]",
    "phone": "[TELÉFONO]",
    "alias": "[ALIAS]",
    "name": "[NOMBRE]",
}


def redact(text: str) -> str:
    redacted = CBU_RE.sub(_TOKEN["cbu"], text)
    redacted = PHONE_RE.sub(_TOKEN["phone"], redacted)
    redacted = ALIAS_RE.sub(lambda match: f"{match.group(1)} {_TOKEN['alias']}", redacted)
    redacted = NAME_RE.sub(lambda match: f"{match.group(1)} {_TOKEN['name']}", redacted)
    return redacted
