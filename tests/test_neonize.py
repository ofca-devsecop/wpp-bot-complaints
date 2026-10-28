import asyncio
import threading
from datetime import UTC, datetime

import pytest
from neonize.events import MessageEv
from neonize.proto.Neonize_pb2 import JID
from neonize.proto.Neonize_pb2 import Message as EventMessage

from sicar_bot.adapters.neonize import NeonizeMessaging, to_inbound
from sicar_bot.ports.messaging import MediaKind

WA_JID = "549111@s.whatsapp.net"


def _jid(user: str = "549111", server: str = "s.whatsapp.net") -> JID:
    return JID(
        User=user,
        Server=server,
        Device=0,
        RawAgent=0,
        Integrator=0,
        IsEmpty=False,
    )


def _event(
    *,
    text: str | None = None,
    image: dict | None = None,
    document: dict | None = None,
    from_me: bool = False,
    group: bool = False,
    message_id: str = "MSG1",
    timestamp: int = 1_700_000_000_000,
) -> EventMessage:
    event = EventMessage()
    event.Info.ID = message_id
    event.Info.Timestamp = timestamp
    event.Info.MessageSource.Chat.CopyFrom(_jid())
    event.Info.MessageSource.IsFromMe = from_me
    event.Info.MessageSource.IsGroup = group
    if text is not None:
        event.Message.conversation = text
    if image is not None:
        for key, value in image.items():
            setattr(event.Message.imageMessage, key, value)
    if document is not None:
        for key, value in document.items():
            setattr(event.Message.documentMessage, key, value)
    return event


def _download(data: bytes):
    return lambda _event: data


def test_text_message_is_normalized():
    message = to_inbound(_event(text="me hackearon"), download=_download(b""))

    assert message is not None
    assert message.wa_jid == WA_JID
    assert message.message_id == "MSG1"
    assert message.timestamp == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert message.text == "me hackearon"
    assert message.media is None


def test_timestamp_in_seconds_is_accepted():
    message = to_inbound(_event(text="hola", timestamp=1_700_000_000), download=_download(b""))

    assert message is not None
    assert message.timestamp == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_own_message_is_ignored():
    assert to_inbound(_event(text="eco", from_me=True), download=_download(b"")) is None


def test_group_message_is_ignored():
    assert to_inbound(_event(text="hola", group=True), download=_download(b"")) is None


def test_message_without_content_is_ignored():
    assert to_inbound(_event(), download=_download(b"")) is None


def test_image_is_downloaded():
    event = _event(
        image={
            "mimetype": "image/jpeg",
            "caption": "comprobante",
            "fileLength": 4,
        }
    )

    message = to_inbound(event, download=_download(b"JPEG"))

    assert message is not None
    assert message.text == "comprobante"
    assert message.media is not None
    assert message.media.kind is MediaKind.IMAGE
    assert message.media.mime_type == "image/jpeg"
    assert message.media.data == b"JPEG"
    assert message.media.caption == "comprobante"
    assert message.media.filename is None


def test_oversized_media_is_skipped_but_text_kept():
    event = _event(
        image={"mimetype": "image/png", "caption": "foto", "fileLength": 20},
    )

    message = to_inbound(event, download=_download(b"x" * 20), max_media_bytes=10)

    assert message is not None
    assert message.text == "foto"
    assert message.media is None


def test_download_failure_keeps_text():
    def boom(_event):
        raise RuntimeError("network down")

    message = to_inbound(_event(text="mirá esto"), download=boom)

    assert message is not None
    assert message.text == "mirá esto"
    assert message.media is None


def test_document_is_downloaded_with_filename():
    event = _event(
        document={
            "mimetype": "application/pdf",
            "fileName": "denuncia.pdf",
            "fileLength": 3,
        }
    )

    message = to_inbound(event, download=_download(b"PDF"))

    assert message is not None
    assert message.media is not None
    assert message.media.kind is MediaKind.DOCUMENT
    assert message.media.mime_type == "application/pdf"
    assert message.media.filename == "denuncia.pdf"
    assert message.media.data == b"PDF"


class FakeClient:
    def __init__(self) -> None:
        self.handlers = {}
        self.qr_handler = None
        self.sent = []
        self.calls = []
        self.connect_started = threading.Event()
        self._release = threading.Event()

    def qr(self, handler):
        self.qr_handler = handler

    def event(self, event_type):
        def register(func):
            self.handlers[event_type] = func
            return func

        return register

    def connect(self) -> None:
        self.connect_started.set()
        self._release.wait(5)

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def stop(self) -> None:
        self.calls.append("stop")
        self._release.set()

    def send_message(self, to: str, text: str) -> None:
        self.sent.append((to, text))

    def download_any(self, _event) -> bytes:
        return b"JPEG"


async def _started(adapter: NeonizeMessaging, fake: FakeClient) -> None:
    await adapter.start()
    assert fake.connect_started.wait(2)


async def test_adapter_delivers_inbound_to_handler():
    fake = FakeClient()
    received = asyncio.Queue()
    adapter = NeonizeMessaging("unused.db", client=fake)
    adapter.on_message(received.put)

    await _started(adapter, fake)
    fake.handlers[MessageEv](fake, _event(text="hola"))

    message = await asyncio.wait_for(received.get(), 2)
    assert message.text == "hola"
    assert message.wa_jid == WA_JID

    await adapter.stop()
    assert fake.calls == ["disconnect", "stop"]


async def test_adapter_sends_text():
    fake = FakeClient()
    adapter = NeonizeMessaging("unused.db", client=fake)
    await _started(adapter, fake)

    await adapter.send_text(WA_JID, "recibido")

    assert fake.sent == [(WA_JID, "recibido")]
    await adapter.stop()


async def test_send_before_start_fails():
    adapter = NeonizeMessaging("unused.db", client=FakeClient())

    with pytest.raises(RuntimeError):
        await adapter.send_text(WA_JID, "hola")


async def test_handler_failure_does_not_stop_consumer():
    fake = FakeClient()
    received = asyncio.Queue()

    async def handler(message):
        if message.text == "boom":
            raise RuntimeError("handler bug")
        await received.put(message)

    adapter = NeonizeMessaging("unused.db", client=fake)
    adapter.on_message(handler)
    await _started(adapter, fake)

    fake.handlers[MessageEv](fake, _event(text="boom", message_id="A"))
    fake.handlers[MessageEv](fake, _event(text="sigue", message_id="B"))

    message = await asyncio.wait_for(received.get(), 2)
    assert message.text == "sigue"
    await adapter.stop()


async def test_stop_without_start_is_a_noop():
    adapter = NeonizeMessaging("unused.db")

    await adapter.stop()
