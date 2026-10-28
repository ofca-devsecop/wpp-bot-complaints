import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from google.genai import types
from pydantic import BaseModel

from sicar_bot.config import get_settings
from sicar_bot.domain.catalog import AttackMedium, Province, Severity, ThreatType
from sicar_bot.domain.extraction import (
    ReceiptExtraction,
    WireExtraction,
    WireReceipt,
    to_extraction,
    to_receipt,
)
from sicar_bot.domain.redaction import redact
from sicar_bot.ports.extractor import (
    ExtractionResult,
    ExtractorError,
    ExtractorPort,
    ImagePart,
)

PROMPT_VERSION = "extract-v1"
RECEIPT_PROMPT_VERSION = "receipt-v1"
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

SYSTEM_PROMPT = f"""Sos un extractor de denuncias de ciberdelitos en Argentina.
Recibís el relato de una víctima y devolvés JSON con los campos pedidos.
Para cada campo devolvé `value` y `confidence` entre 0 y 1.
Si el dato no surge del texto, dejá `value` vacío y `confidence` en 0. No inventes datos.
Usá exactamente estos valores:

Tipo de amenaza: {", ".join(f"{item.value} ({item.label})" for item in ThreatType)}
Severidad: {", ".join(f"{item.value} ({item.label})" for item in Severity)}
Provincia: {", ".join(f"{item.value} ({item.label})" for item in Province)}
Medio del atacante: {", ".join(f"{item.value} ({item.label})" for item in AttackMedium)}
Pérdida económica: "si" o "no".

`evidence_summary` resume en una frase los datos de comprobantes (monto, fecha, banco,
alias) que aparezcan en el relato o en las imágenes."""

VISION_SYSTEM_PROMPT = """Sos un extractor de comprobantes de transferencia de Argentina.
Recibís una o más imágenes y devolvés JSON con los datos visibles.
Si un dato no se ve con claridad, dejalo vacío. No inventes datos.
`amount` solo con dígitos y punto decimal; `currency` como código (ARS, USD);
`date` en formato AAAA-MM-DD si es determinable; `account` con alias, CBU o CVU tal
como figura; `operation_number` con el número de operación; `summary` con una frase
que resuma el comprobante."""

RECEIPT_PROMPT = "Extraé los datos de este comprobante."


_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_MARKERS = ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code in _TRANSIENT_CODES:
        return True
    text = str(exc).upper()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _parse_model[M: BaseModel](response: Any, model_cls: type[M]) -> M:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, model_cls):
        return parsed
    if isinstance(parsed, dict):
        return model_cls.model_validate(parsed)
    text = getattr(response, "text", None)
    if not text:
        raise ExtractorError("respuesta del LLM vacía")
    try:
        return model_cls.model_validate_json(text)
    except ValueError as exc:
        raise ExtractorError(f"JSON inválido del LLM: {exc}") from exc


class GeminiExtractor(ExtractorPort):
    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
        vision_model: str | None = None,
        vision_timeout: float | None = None,
        prompt_version: str = PROMPT_VERSION,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        settings = get_settings()
        self._client = client or self._default_client()
        self.model = model or settings.gemini_model
        self.vision_model = vision_model or settings.gemini_vision_model
        self.prompt_version = prompt_version
        self._sleep = sleep
        self._vision_timeout = (
            vision_timeout if vision_timeout is not None else settings.gemini_vision_timeout_s
        )
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=WireExtraction,
            temperature=0.0,
        )
        self._vision_config = types.GenerateContentConfig(
            system_instruction=VISION_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=WireReceipt,
            temperature=0.0,
        )

    @staticmethod
    def _default_client() -> Any:
        from google import genai

        return genai.Client(api_key=get_settings().gemini_api_key)

    async def extract(self, text: str, images: Sequence[ImagePart] = ()) -> ExtractionResult:
        redacted = redact(text)
        contents: list[Any] = [
            types.Part(
                text=(
                    "Extraé los datos de esta denuncia. Si un dato no surge, "
                    f"dejá value vacío.\n\nDenuncia:\n{redacted}"
                )
            )
        ]
        contents.extend(
            types.Part.from_bytes(data=image.data, mime_type=image.mime_type) for image in images
        )

        response = await self._generate(contents, model=self.model, config=self._config)
        wire = _parse_model(response, WireExtraction)
        return ExtractionResult(
            extraction=to_extraction(wire),
            redacted_text=redacted,
            input_hash=hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
            model=self.model,
            prompt_version=self.prompt_version,
        )

    async def extract_receipt(self, images: Sequence[ImagePart]) -> ReceiptExtraction | None:
        if not images:
            return None
        contents: list[Any] = [types.Part(text=RECEIPT_PROMPT)]
        contents.extend(
            types.Part.from_bytes(data=image.data, mime_type=image.mime_type) for image in images
        )
        try:
            async with asyncio.timeout(self._vision_timeout):
                response = await self._generate(
                    contents, model=self.vision_model, config=self._vision_config
                )
            return to_receipt(_parse_model(response, WireReceipt))
        except Exception:
            return None

    async def _generate(self, contents: list[Any], *, model: str, config: Any) -> Any:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await self._client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1 or not _is_transient(exc):
                    raise
                await self._sleep(RETRY_BASE_DELAY * 2**attempt)
        raise ExtractorError("sin respuesta del LLM")
