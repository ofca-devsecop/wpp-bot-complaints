"""Puerto de mensajería: WhatsApp entra y sale por acá."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class MediaKind(StrEnum):
    IMAGE = "image"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class InboundMedia:
    """Un archivo recibido, ya descargado y en memoria."""

    kind: MediaKind
    mime_type: str
    data: bytes
    filename: str | None = None
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Mensaje entrante normalizado, independiente del transporte."""

    wa_jid: str
    message_id: str
    timestamp: datetime
    text: str | None = None
    media: InboundMedia | None = None


MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class MessagingPort(Protocol):
    """Contrato mínimo que el flujo conversacional necesita de WhatsApp."""

    def on_message(self, handler: MessageHandler) -> None:
        """Registra el consumidor de mensajes entrantes."""
        ...

    async def start(self) -> None:
        """Conecta el transporte. No espera a que haya vinculación."""
        ...

    async def send_text(self, wa_jid: str, text: str) -> None:
        """Envía un texto plano a un contacto."""
        ...

    async def stop(self) -> None:
        """Desconecta y libera los recursos del transporte."""
        ...
