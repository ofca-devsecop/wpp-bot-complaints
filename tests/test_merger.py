import asyncio
from datetime import UTC, datetime

from sicar_bot.ports.messaging import InboundMedia, InboundMessage, MediaKind
from sicar_bot.services.merger import MessageMerger

JID = "2319416565846@lid"
OTHER_JID = "5491155550000@s.whatsapp.net"
WINDOW = 0.02


def _inbound(
    text: str = "",
    *,
    medias: tuple[InboundMedia, ...] = (),
    message_id: str = "m1",
    jid: str = JID,
    timestamp: datetime = datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
) -> InboundMessage:
    return InboundMessage(
        wa_jid=jid,
        message_id=message_id,
        timestamp=timestamp,
        text=text or None,
        medias=medias,
    )


def _photo(marker: bytes = b"\x89PNG\r\n\x1a\nfoto") -> InboundMedia:
    return InboundMedia(kind=MediaKind.IMAGE, mime_type="image/png", data=marker)


class Recorder:
    def __init__(self) -> None:
        self.turns: list[InboundMessage] = []

    async def __call__(self, message: InboundMessage) -> None:
        self.turns.append(message)


async def _wait_for(recorder: Recorder, count: int) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1
    while len(recorder.turns) < count:
        if loop.time() > deadline:
            raise AssertionError(f"esperaba {count} turnos, llegaron {len(recorder.turns)}")
        await asyncio.sleep(0.005)


async def test_photo_then_text_is_one_turn():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)

    await merger(_inbound(medias=(_photo(),), message_id="foto"))
    await merger(_inbound("me estafaron por whatsapp", message_id="texto"))
    await _wait_for(recorder, 1)

    assert len(recorder.turns) == 1
    turn = recorder.turns[0]
    assert turn.text == "me estafaron por whatsapp"
    assert len(turn.medias) == 1
    assert turn.message_id == "texto"
    merger.aclose()


async def test_photo_alone_is_delivered_when_the_window_expires():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)

    await merger(_inbound(medias=(_photo(),)))
    assert recorder.turns == []
    await _wait_for(recorder, 1)

    assert len(recorder.turns) == 1
    assert recorder.turns[0].text is None
    assert len(recorder.turns[0].medias) == 1
    merger.aclose()


async def test_two_texts_are_two_turns():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)

    await merger(_inbound("hola", message_id="a"))
    await merger(_inbound("quiero denunciar", message_id="b"))

    assert [turn.text for turn in recorder.turns] == ["hola", "quiero denunciar"]
    merger.aclose()


async def test_two_photos_then_text_is_one_turn_with_both_medias():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)

    await merger(_inbound(medias=(_photo(b"uno"),), message_id="f1"))
    await merger(_inbound(medias=(_photo(b"dos"),), message_id="f2"))
    await merger(_inbound("mirá", message_id="t"))

    assert len(recorder.turns) == 1
    assert recorder.turns[0].text == "mirá"
    assert [media.data for media in recorder.turns[0].medias] == [b"uno", b"dos"]
    merger.aclose()


async def test_other_contact_does_not_merge():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)

    await merger(_inbound(medias=(_photo(),), jid=JID))
    await merger(_inbound("otra persona", jid=OTHER_JID))

    assert [turn.wa_jid for turn in recorder.turns] == [OTHER_JID]
    await _wait_for(recorder, 2)
    assert [turn.wa_jid for turn in recorder.turns] == [OTHER_JID, JID]
    merger.aclose()


async def test_merged_turn_keeps_the_earliest_timestamp():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)
    early = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    late = datetime(2026, 10, 28, 12, 1, tzinfo=UTC)

    await merger(_inbound(medias=(_photo(),), timestamp=early))
    await merger(_inbound("texto", timestamp=late))

    assert recorder.turns[0].timestamp == early
    merger.aclose()


async def test_close_cancels_pending_turns():
    recorder = Recorder()
    merger = MessageMerger(recorder, window=WINDOW)

    await merger(_inbound(medias=(_photo(),)))
    merger.aclose()
    await asyncio.sleep(WINDOW * 4)

    assert recorder.turns == []
