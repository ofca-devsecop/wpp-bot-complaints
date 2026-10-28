"""Agrupa mensajes consecutivos del mismo contacto en un solo turno."""

import asyncio
import logging
from dataclasses import replace

from sicar_bot.ports.messaging import InboundMessage, MessageHandler

log = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 3.0


def _merge(first: InboundMessage, second: InboundMessage) -> InboundMessage:
    texts = [text for text in (first.text, second.text) if text and text.strip()]
    return replace(
        second,
        text="\n".join(texts) or None,
        medias=first.medias + second.medias,
        timestamp=min(first.timestamp, second.timestamp),
    )


def _holds_turn(message: InboundMessage) -> bool:
    """Solo se retiene media sin texto: dos respuestas seguidas son dos turnos."""
    return message.has_media and not (message.text or "").strip()


class MessageMerger:
    """Ventana corta por JID: una foto sin texto espera al texto que la explica.

    Un mensaje con media y sin texto no se entrega solo: abre una ventana de
    ``window`` segundos. Si llega otro mensaje del mismo JID, se fusionan y se
    entrega un único turno. Si la ventana vence, el pendiente se entrega igual,
    así una foto sola también recibe respuesta.
    """

    def __init__(
        self,
        handler: MessageHandler,
        *,
        window: float = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._handler = handler
        self._window = window
        self._pending: dict[str, InboundMessage] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    async def __call__(self, inbound: InboundMessage) -> None:
        pending = self._take(inbound.wa_jid)
        merged = _merge(pending, inbound) if pending is not None else inbound
        if _holds_turn(merged):
            self._pending[inbound.wa_jid] = merged
            loop = asyncio.get_running_loop()
            self._timers[inbound.wa_jid] = loop.call_later(
                self._window, self._flush, inbound.wa_jid
            )
            return
        await self._handler(merged)

    def aclose(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._pending.clear()

    def _take(self, wa_jid: str) -> InboundMessage | None:
        timer = self._timers.pop(wa_jid, None)
        if timer is not None:
            timer.cancel()
        return self._pending.pop(wa_jid, None)

    def _flush(self, wa_jid: str) -> None:
        message = self._take(wa_jid)
        if message is not None:
            asyncio.get_running_loop().create_task(self._deliver(message))

    async def _deliver(self, message: InboundMessage) -> None:
        try:
            await self._handler(message)
        except Exception:
            log.exception("merged message handling failed", extra={"wa_jid": message.wa_jid})
