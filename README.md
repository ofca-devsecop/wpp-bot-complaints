# SICAR-C Bot

Bot de WhatsApp que recibe denuncias de ciberdelitos en texto libre e imágenes, completa los datos faltantes con una encuesta y los expone a SICAR-C por API y CSV.

## Desarrollo

```bash
cp .env.example .env
uv sync
uv run pytest
uv run uvicorn sicar_bot.main:app --reload
curl http://127.0.0.1:8000/health
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

## Estado

En construcción. Plan y tareas en `tasks/`.
