from fastapi import FastAPI

from sicar_bot import __version__
from sicar_bot.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    is_prod = settings.env == "prod"

    app = FastAPI(
        title="SICAR-C Bot",
        version=__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
