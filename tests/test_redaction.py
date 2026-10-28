from sicar_bot.domain.redaction import redact


def test_redacts_phone() -> None:
    assert redact("Mi teléfono es +54 9 351 123-4567") == "Mi teléfono es [TELÉFONO]"


def test_redacts_cbu() -> None:
    assert redact("Transferí a CBU 0000003100010000000001") == "Transferí a CBU [CBU]"


def test_redacts_grouped_cbu() -> None:
    assert redact("CBU 0000 0031 0001 0000 0000 01") == "CBU [CBU]"


def test_redacts_alias() -> None:
    assert redact("Mandá a alias juan.perez.bna") == "Mandá a alias [ALIAS]"


def test_redacts_name_but_keeps_place() -> None:
    assert redact("Me llamo Ana Gómez y vivo en Córdoba") == "Me llamo [NOMBRE] y vivo en Córdoba"


def test_leaves_text_without_pii_untouched() -> None:
    text = "Me estafaron con una transferencia de $150.000 el 12/10/2026"
    assert redact(text) == text
