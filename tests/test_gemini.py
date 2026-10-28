import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from sicar_bot.adapters.gemini import MAX_ATTEMPTS, RETRY_BASE_DELAY, GeminiExtractor
from sicar_bot.domain.catalog import ThreatType
from sicar_bot.domain.extraction import WireExtraction, WireReceipt
from sicar_bot.ports.extractor import ImagePart


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.models = FakeModels(responses)
        self.aio = SimpleNamespace(models=self.models)


class RateLimitError(Exception):
    code = 429


def response(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(text=json.dumps(payload), parsed=None)


PAYLOAD = {
    "threat_type": {"value": "phishing", "confidence": 0.9},
    "severity": {"value": "alta", "confidence": 0.8},
    "province": {"value": "cordoba", "confidence": 0.75},
    "evidence_summary": "transferencia de $150.000",
}


async def test_extract_redacts_before_sending() -> None:
    client = FakeClient([response(PAYLOAD)])
    extractor = GeminiExtractor(client, model="test-model")

    result = await extractor.extract("Me llamo Ana, mi tel 351 123 4567")

    call = client.models.calls[0]
    sent_text = call["contents"][0].text
    assert "351 123 4567" not in sent_text
    assert "[TELÉFONO]" in sent_text
    assert "[NOMBRE]" in sent_text
    assert result.redacted_text == "Me llamo [NOMBRE], mi tel [TELÉFONO]"
    assert result.input_hash == hashlib.sha256(result.redacted_text.encode()).hexdigest()
    assert call["model"] == "test-model"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is WireExtraction


async def test_extract_maps_payload_to_domain() -> None:
    extractor = GeminiExtractor(FakeClient([response(PAYLOAD)]), model="test-model")

    result = await extractor.extract("me hicieron phishing")

    assert result.extraction.threat_type.value is ThreatType.PHISHING
    assert result.extraction.province.value.value == "cordoba"
    assert result.extraction.evidence_summary == "transferencia de $150.000"
    assert result.model == "test-model"
    assert result.prompt_version == "extract-v1"


async def test_invalid_enum_in_payload_becomes_null() -> None:
    payload = {"threat_type": {"value": "inventado", "confidence": 0.99}}
    extractor = GeminiExtractor(FakeClient([response(payload)]), model="test-model")

    result = await extractor.extract("texto")

    assert result.extraction.threat_type.value is None


async def test_retries_rate_limit_with_backoff() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = FakeClient([RateLimitError("429"), RateLimitError("429"), response(PAYLOAD)])
    extractor = GeminiExtractor(client, model="test-model", sleep=fake_sleep)

    result = await extractor.extract("texto")

    assert result.extraction.threat_type.value is ThreatType.PHISHING
    assert len(client.models.calls) == MAX_ATTEMPTS
    assert delays == [RETRY_BASE_DELAY, RETRY_BASE_DELAY * 2]


async def test_gives_up_after_max_attempts() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = FakeClient([RateLimitError("429") for _ in range(MAX_ATTEMPTS)])
    extractor = GeminiExtractor(client, model="test-model", sleep=fake_sleep)

    with pytest.raises(RateLimitError):
        await extractor.extract("texto")

    assert len(client.models.calls) == MAX_ATTEMPTS
    assert len(delays) == MAX_ATTEMPTS - 1


async def test_does_not_retry_other_errors() -> None:
    client = FakeClient([ValueError("boom")])
    extractor = GeminiExtractor(client, model="test-model")

    with pytest.raises(ValueError):
        await extractor.extract("texto")

    assert len(client.models.calls) == 1


async def test_images_are_sent_as_parts() -> None:
    client = FakeClient([response(PAYLOAD)])
    extractor = GeminiExtractor(client, model="test-model")

    await extractor.extract("texto", [ImagePart("image/png", b"png-bytes")])

    contents = client.models.calls[0]["contents"]
    assert len(contents) == 2
    assert isinstance(contents[1], types.Part)


RECEIPT_PAYLOAD = {
    "amount": "150000.00",
    "currency": "ARS",
    "date": "2026-10-12",
    "bank": "Banco Nación",
    "account": "juan.perez.bna",
    "operation_number": "OP-123456",
    "summary": "transferencia de $150.000 a alias juan.perez.bna",
}


async def test_receipt_extraction_returns_structured_fields() -> None:
    client = FakeClient([response(RECEIPT_PAYLOAD)])
    extractor = GeminiExtractor(client, vision_model="test-vision")

    receipt = await extractor.extract_receipt([ImagePart("image/png", b"png")])

    assert receipt is not None
    assert receipt.amount == "150000.00"
    assert receipt.currency == "ARS"
    assert receipt.date == "2026-10-12"
    assert receipt.bank == "Banco Nación"
    assert receipt.account == "juan.perez.bna"
    assert receipt.operation_number == "OP-123456"
    assert receipt.summary == "transferencia de $150.000 a alias juan.perez.bna"
    call = client.models.calls[0]
    assert call["model"] == "test-vision"
    assert call["config"].response_schema is WireReceipt
    assert isinstance(call["contents"][0], types.Part)
    assert isinstance(call["contents"][1], types.Part)


async def test_receipt_without_images_makes_no_call() -> None:
    client = FakeClient([])
    extractor = GeminiExtractor(client)

    assert await extractor.extract_receipt([]) is None
    assert client.models.calls == []


async def test_receipt_provider_error_returns_none() -> None:
    client = FakeClient([ValueError("boom")])
    extractor = GeminiExtractor(client)

    assert await extractor.extract_receipt([ImagePart("image/png", b"png")]) is None


async def test_receipt_timeout_returns_none() -> None:
    class SlowModels(FakeModels):
        async def generate_content(self, **kwargs: Any) -> Any:
            await asyncio.sleep(0.2)
            return response(RECEIPT_PAYLOAD)

    client = FakeClient([])
    client.models = SlowModels([])
    client.aio = SimpleNamespace(models=client.models)
    extractor = GeminiExtractor(client, vision_timeout=0.01)

    assert await extractor.extract_receipt([ImagePart("image/png", b"png")]) is None


async def test_receipt_invalid_json_returns_none() -> None:
    client = FakeClient([SimpleNamespace(text="no es json", parsed=None)])
    extractor = GeminiExtractor(client)

    assert await extractor.extract_receipt([ImagePart("image/png", b"png")]) is None


async def test_receipt_empty_fields_become_none() -> None:
    client = FakeClient([response({"amount": "  ", "bank": ""})])
    extractor = GeminiExtractor(client)

    receipt = await extractor.extract_receipt([ImagePart("image/png", b"png")])

    assert receipt is not None
    assert receipt.amount is None
    assert receipt.bank is None
    assert receipt.summary is None


class UnavailableError(Exception):
    code = 503


async def test_retries_server_unavailable() -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = FakeClient([UnavailableError("503 UNAVAILABLE"), response(PAYLOAD)])
    extractor = GeminiExtractor(client, model="test-model", sleep=fake_sleep)

    result = await extractor.extract("texto")

    assert result.extraction.threat_type.value is ThreatType.PHISHING
    assert delays == [RETRY_BASE_DELAY]
