"""Marca como abandonados los borradores sin actividad (72 h por defecto)."""

import logging

from sicar_bot.adapters.db.session import default_session_factory
from sicar_bot.services.maintenance import abandon_stale

logging.basicConfig(level=logging.INFO)


def main() -> None:
    factory = default_session_factory()
    count = abandon_stale(factory)
    print(f"borradores abandonados: {count}")


if __name__ == "__main__":
    main()
