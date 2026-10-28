"""Comandos de texto disponibles en cualquier estado de la conversación."""

from enum import StrEnum

from sicar_bot.domain.catalog import normalize


class Command(StrEnum):
    HELP = "ayuda"
    STATUS = "estado"
    CANCEL = "cancelar"
    RESTART = "reiniciar"


_ALIASES: dict[str, Command] = {
    "ayuda": Command.HELP,
    "help": Command.HELP,
    "estado": Command.STATUS,
    "cancelar": Command.CANCEL,
    "reiniciar": Command.RESTART,
}


def parse(text: str) -> Command | None:
    """Devuelve el comando si el primer token lo es; si no, ``None``."""
    normalized = normalize(text)
    if not normalized:
        return None
    return _ALIASES.get(normalized.split()[0])
