import pathlib

import pgserver
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"))
    try:
        yield server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    finally:
        server.cleanup()


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_roundtrip(database_url: str) -> None:
    config = alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        "conversations",
        "evidence",
        "llm_runs",
        "messages",
        "reports",
    }

    with engine.connect() as connection:
        assert connection.execute(text("select nextval('public_code_seq')")).scalar_one() == 1
        assert connection.execute(text("select nextval('public_code_seq')")).scalar_one() == 2

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "insert into reports (id, wa_jid, status) "
                    "values (gen_random_uuid(), '5491100000000@s.whatsapp.net', 'inventado')"
                )
            )
        connection.rollback()

    engine.dispose()

    command.downgrade(config, "base")

    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()
