"""Adaptador de WhatsApp sobre Neonize (whatsmeow, API no oficial).

Sólo traduce eventos del transporte al puerto de mensajería: no contiene
lógica conversacional ni de negocio.
"""

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv
from neonize.utils import extract_text

from sicar_bot.ports.messaging import (
    InboundMedia,
    InboundMessage,
    MediaKind,
    MessageHandler,
)

log = logging.getLogger(__name__)

DEFAULT_MAX_MEDIA_BYTES = 10 * 1024 * 1024
DEFAULT_QUEUE_SIZE = 32
STOP_TIMEOUT_S = 5.0


def _jid_str(jid) -> str:
    return f"{jid.User}@{jid.Server}"


def _timestamp(raw: int) -> datetime:
    """Neonize entrega milisegundos; se aceptan segundos por si cambia."""
    if not raw:
        return datetime.now(UTC)
    if raw >= 100_000_000_000:
        return datetime.fromtimestamp(raw / 1000, tz=UTC)
    return datetime.fromtimestamp(raw, tz=UTC)


def _extract_media(
    message,
    *,
    download: Callable[[object], bytes | None],
    max_media_bytes: int,
) -> InboundMedia | None:
    image = message.imageMessage
    if image.ListFields():
        kind = MediaKind.IMAGE
        mime_type = image.mimetype or "image/jpeg"
        caption = image.caption or None
        file_length = image.fileLength
        filename = None
    else:
        document = message.documentMessage
        if not document.ListFields():
            return None
        kind = MediaKind.DOCUMENT
        mime_type = document.mimetype or "application/octet-stream"
        caption = document.caption or None
        file_length = document.fileLength
        filename = document.fileName or None

    if file_length and file_length > max_media_bytes:
        log.warning("media ignored: %s bytes over limit", file_length)
        return None

    try:
        data = download(message)
    except Exception:
        log.warning("media download failed", exc_info=True)
        return None

    if data is None:
        return None
    if len(data) > max_media_bytes:
        log.warning("media ignored: downloaded %s bytes over limit", len(data))
        return None
    return InboundMedia(
        kind=kind,
        mime_type=mime_type,
        data=data,
        filename=filename,
        caption=caption,
    )


def to_inbound(
    event,
    *,
    download: Callable[[object], bytes | None],
    max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> InboundMessage | None:
    """Convierte un evento de Neonize en un mensaje del puerto.

    Devuelve ``None`` para mensajes propios, de grupo, sin contenido útil o
    sin identificador. Los envoltorios (efímeros, ver una vez) no se abren.
    """
    info = event.Info
    source = info.MessageSource
    if source.IsFromMe or source.IsGroup or not info.ID:
        return None

    message = event.Message
    text = extract_text(message) or None
    media = _extract_media(message, download=download, max_media_bytes=max_media_bytes)
    medias = (media,) if media is not None else ()
    if text is None and not medias:
        return None

    timestamp = _timestamp(info.Timestamp)
    return InboundMessage(
        wa_jid=_jid_str(source.Chat),
        message_id=info.ID,
        timestamp=timestamp,
        text=text,
        medias=medias,
    )


class NeonizeMessaging:
    """Implementación del puerto sobre un cliente Neonize."""

    def __init__(
        self,
        session_db: str,
        *,
        max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        qr_callback: Callable[[bytes], None] | None = None,
        client: NewClient | None = None,
    ) -> None:
        self._session_db = session_db
        self._max_media_bytes = max_media_bytes
        self._queue_size = queue_size
        self._qr_callback = qr_callback
        self._client = client
        self._handler: MessageHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[InboundMessage] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        if self._thread is not None:
            return

        Path(self._session_db).parent.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self._queue_size)
        self._consumer = asyncio.create_task(self._consume())

        client = self._client or NewClient(self._session_db)
        self._client = client
        if self._qr_callback is not None:
            client.qr(lambda _client, data: self._qr_callback(data))

        @client.event(MessageEv)
        def _on_event(_client, event) -> None:
            self._dispatch(event)

        @client.event(ConnectedEv)
        def _on_connected(_client, _event) -> None:
            log.info("whatsapp connected")

        self._thread = threading.Thread(target=self._run, name="whatsapp", daemon=True)
        self._thread.start()

    async def send_text(self, wa_jid: str, text: str) -> None:
        client = self._client
        if client is None or self._thread is None:
            raise RuntimeError("messaging not started")
        await asyncio.to_thread(client.send_message, wa_jid, text)

    async def stop(self) -> None:
        """Corta el transporte lo mejor posible.

        Neonize no garantiza que ``connect()`` retorne al desconectar, así que
        el hilo es daemon y el cierre del proceso termina de liberar el socket.
        """
        client = self._client
        self._client = None
        if client is not None:
            for close in (client.disconnect, client.stop):
                try:
                    await asyncio.to_thread(close)
                except Exception:
                    log.warning("whatsapp shutdown step failed", exc_info=True)

        thread = self._thread
        self._thread = None
        if thread is not None:
            await asyncio.to_thread(thread.join, STOP_TIMEOUT_S)
            if thread.is_alive():
                log.warning("whatsapp thread still alive; process exit will close it")

        consumer = self._consumer
        self._consumer = None
        if consumer is not None:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer
        self._queue = None

    def _run(self) -> None:
        try:
            self._client.connect()
        except Exception:
            log.exception("whatsapp client stopped unexpectedly")

    def _dispatch(self, event) -> None:
        client = self._client
        loop = self._loop
        if client is None or loop is None or loop.is_closed():
            return
        try:
            message = to_inbound(
                event,
                download=client.download_any,
                max_media_bytes=self._max_media_bytes,
            )
        except Exception:
            log.exception("could not parse inbound message")
            return
        if message is None:
            return
        loop.call_soon_threadsafe(self._enqueue, message)

    def _enqueue(self, message: InboundMessage) -> None:
        queue = self._queue
        if queue is None:
            return
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            log.warning("inbound queue full, dropping %s", message.message_id)

    async def _consume(self) -> None:
        queue = self._queue
        assert queue is not None
        while True:
            message = await queue.get()
            handler = self._handler
            if handler is None:
                continue
            try:
                await handler(message)
            except Exception:
                log.exception("handler failed for %s", message.message_id)
