"""Prueba manual del adaptador de WhatsApp contra un número real.

Uso:
    uv run python scripts/whatsapp_smoke.py

La primera vez imprime un QR en la terminal: escanealo desde WhatsApp >
Dispositivos vinculados. La sesión queda en NEONIZE_SESSION_DB y las
siguientes corridas no piden QR. Escribile al número vinculado: el script
responde "recibí tu mensaje" y muestra lo que llegó. Ctrl-C para salir.
"""

import asyncio
import logging

from sicar_bot.adapters.neonize import NeonizeMessaging
from sicar_bot.config import get_settings


async def main() -> None:
    settings = get_settings()
    messaging = NeonizeMessaging(
        settings.neonize_session_db,
        max_media_bytes=settings.whatsapp_max_media_bytes,
    )

    async def handler(message) -> None:
        media = message.media
        print(
            f"IN {message.wa_jid} {message.message_id} text={message.text!r} "
            f"media={None if media is None else (media.kind, media.mime_type, len(media.data))}"
        )
        await messaging.send_text(message.wa_jid, "recibí tu mensaje")

    messaging.on_message(handler)
    await messaging.start()
    print("esperando mensajes (Ctrl-C para salir)")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await messaging.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("whatsmeow").setLevel(logging.WARNING)
    asyncio.run(main())
