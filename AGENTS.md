# AGENTS.md

Bot de WhatsApp que recibe denuncias de ciberdelitos, extrae los campos del relato con
Gemini, completa lo que falta con una encuesta guiada determinística y deja la denuncia
lista para que SICAR-C la lea por API/CSV.

Plan completo y decisiones: `tasks/plan.md`. Estado tarea por tarea: `tasks/todo.md`.

## Comandos

```bash
uv sync                                    # dependencias
uv run pytest -q                           # suite completa (Postgres real)
uv run pytest -q tests/test_survey.py      # un archivo
uv run ruff check src tests scripts        # lint
uv run ruff format src tests scripts       # formato
uv run alembic upgrade head                # migraciones (necesita DATABASE_URL)
uv run python scripts/whatsapp_smoke.py    # QR + consola, prueba manual del canal
uv run python scripts/abandon_drafts.py    # job de borradores vencidos (72 h)
```

Antes de cada commit: `ruff check` + `ruff format` + `pytest -q` en verde.

## Tests

- Los tests corren contra **Postgres real** (`pgserver`, binario embebido). No hay mocks de
  base de datos: si algo no se puede probar así, es señal de diseño, no de test.
- `tests/conftest.py` expone `session`, `session_factory` (savepoints sobre la misma conexión)
  y `pg_url`. Una `session` nueva en el test ve lo que el servicio commiteó.
- El esquema de test se crea con `alembic upgrade head`, no con `create_all`: una migración
  rota rompe la suite.
- Los dobles viven en el test que los usa (`FakeMessaging`, `FakeExtractor`). No hay framework
  de mocks.

## Arquitectura

```
src/sicar_bot/
  domain/      reglas puras: catálogo, encuesta, mensajes, comandos, redacción
  ports/       contratos (Protocol) que el dominio/servicios necesitan
  adapters/    implementaciones: neonize, gemini, db, evidence
  services/    orquestación: conversation (flujo), maintenance (jobs)
```

Reglas de dependencia:

- `domain/` no importa `adapters/`, `services/` ni librerías de infraestructura.
- `services/` depende de `ports/`, nunca de `adapters/` concretos.
- `adapters/` implementa `ports/`; puede importar `domain/` para mapear tipos.
- Los textos que ve el usuario viven en `domain/messages.py`. Cambiarlos no debe tocar lógica.

## Invariantes

- **Idempotencia**: todo mensaje entra por `MessageRepository.record_inbound` con
  `ON CONFLICT DO NOTHING` sobre `wa_message_id`. Un reintento de WhatsApp no duplica nada.
- **`wa_jid` es la clave de conversación** y hoy llega como LID (`...@lid`). No cambiar la
  clave a medio flujo; resolver el teléfono es un paso aparte.
- **Código público** (`SICAR-YYYY-NNNNNN`) se asigna solo en `ReportRepository.submit`, con la
  secuencia `public_code_seq`. `submit` es idempotente.
- **Evidencia**: máximo 5 archivos por denuncia (lo impone `Survey.add_evidence`) y 10 MB por
  archivo (lo impone `FilesystemEvidenceStore`). El store no duplica el tope de cantidad.
- **Estados de conversación** (`awaiting_first`, `collecting`, `confirming`, `done`) los mueve
  solo `Survey`. El servicio no inventa transiciones.
- **Progreso persistido**: `conversations.evidence_skipped` y `omitted_fields` existen para que
  la encuesta se reconstruya entre mensajes con `Survey.restore`.
- **Rate limit**: 20 mensajes entrantes por minuto por JID, contados sobre `messages`. Al
  excederse se descarta en silencio y se loguea (nunca se responde, para no amplificar).
- **Redacción**: el texto se guarda y se manda al LLM solo después de `redact()`.

## Gotchas verificados

- `neonize` 0.5.2: `info.Timestamp` viene en **milisegundos** (raw ~1.7e12). `_timestamp`
  acepta ambos.
- `client.connect()` **bloquea** y no retorna tras `disconnect()` + `stop()`. Se corre en un
  hilo daemon; el cierre real es terminar el proceso.
- `download_any` recibe el **mensaje interno** (`event.Message`, protobuf `waE2E.Message`), no
  el evento. Pasar el evento falla con `didn't find any attachments in message`.
- Sin `ffmpeg` no hay stickers/notas de voz/miniaturas; texto, fotos y documentos funcionan.
- Gemini free tier devuelve 503/429 con frecuencia: `_is_transient` reintenta con backoff.
  `gemini-3.5-flash` es el modelo que responde hoy.
- Postgres: los enums son `VARCHAR(40)` + CHECK (`native_enum=False`). Comparar con el enum de
  Python, no con strings sueltos.

## Migraciones

- Nunca editar una revisión ya aplicada. Cambio de esquema = nueva revisión en
  `migrations/versions/` con `down_revision` de la última.
- Toda migración necesita `downgrade()` y pasa por `tests/test_migrations.py`.

## Secretos

- `.env` está gitignoreado y en `chmod 600`. La API key de Gemini no se loguea, no se imprime y
  no se commitea.
- El token de la API de SICAR-C se compara en tiempo constante y va acompañado de allowlist de
  IP/CIDR.

## Flujo de trabajo

- Una tarea de `tasks/todo.md` por vez. Al terminarla: marcarla `[x]` y agregar una línea
  "Hecho:" con los archivos y el conteo de tests.
- Commits en español, imperativos y acotados. El autor del repo es
  `ofca <Ofca.devsecop@protonmail.com>` (config local, no global).
- El remoto usa el alias SSH `github-ofca` para no mezclar cuentas con `gh` (que queda logueado
  como el usuario personal).
