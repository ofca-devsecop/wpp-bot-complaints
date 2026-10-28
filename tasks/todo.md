# Task List — Bot SICAR-C

Plan completo en `tasks/plan.md`. Un ítem por tarea. No avanzar con una tarea sin su verificación.

## Fase 0 — Scaffold

- [x] **T1. Estructura del repo y health check**
  - [x] `pyproject.toml` con Python 3.12, FastAPI, SQLAlchemy, Alembic, Neonize, Pydantic, pytest.
  - [x] `config.py` con `pydantic-settings` y `.env.example`.
  - [x] `docker-compose.yml` con app + Postgres 16.
  - [x] `GET /health` responde 200 sin DB.
  - Verificación: `uv run pytest` (1 test) y `uv run uvicorn sicar_bot.main:app` → `curl /health`.
  - Hecho: pytest verde, ruff limpio, `curl /health` → `{"status":"ok"}`. Docker no está instalado en este host: compose queda sin ejecutar.
  - Deps: ninguna. Scope: S (10 archivos).

## Fase 1 — Dominio puro

- [x] **T2. Catálogos y validación**
  - [x] Enums de amenaza, severidad, medio, estado.
  - [x] 24 provincias + mapa de alias (`CABA`, `Bs As`, `Tucuman`, etc.).
  - [x] Normalizador sin acentos ni mayúsculas; ambigüedad devuelve error.
  - Verificación: tests de las 24 provincias, 10 alias y 5 entradas inválidas.
  - Hecho: 58 tests verdes; cubre 24 provincias, 10 alias, 5 inválidos, puntuación y ambigüedad.
  - Deps: T1. Scope: S.

- [x] **T3. Máquina de estados de la encuesta**
  - [x] Estados `awaiting_first → collecting → confirming → done`.
  - [x] Orden de campos y cálculo de faltantes.
  - [x] Reintento por respuesta inválida; `omitir` en opcionales; `cancelar`/`reiniciar`.
  - Verificación: tests table-driven de transición, campo siguiente, inválido y confirmación.
  - Hecho: `domain/survey.py`; 31 tests de encuesta; tope de 5 evidencias (`EVIDENCE_FULL`); set `omitted`.
  - Deps: T2. Scope: M.

### Checkpoint A (T1–T3)
- [x] `pytest` verde.
- [x] Flujo completo simulable sin WhatsApp ni LLM.
- Hecho: 88 tests verdes antes de T4 (89 con migración).

## Fase 2 — Persistencia

- [x] **T4. Modelos y migración**
  - [x] Tablas `reports`, `evidence`, `conversations`, `messages`, `llm_runs`.
  - [x] Secuencia para `public_code` (`SICAR-YYYY-NNNNNN`).
  - [x] `alembic upgrade head` sobre DB vacía.
  - Verificación: migración aplica y revierte; test de constraints únicos.
  - Hecho: `adapters/db/models.py` + `migrations/0001`; verificado contra PostgreSQL 16.2 embebido (`pgserver`, sin Docker): upgrade, secuencia, CHECK de enum y downgrade a `base`.
  - Deps: T1. Scope: M.

- [x] **T5. Repositorios e idempotencia**
  - [x] CRUD de reportes y conversaciones.
  - [x] Inserción de `wa_message_id` duplicado no reprocesa.
  - Verificación: test de round-trip y de mensaje repetido.
  - Hecho: `adapters/db/session.py` + `repositories.py`; upsert idempotente con `ON CONFLICT DO NOTHING ... RETURNING`; `submit` asigna `SICAR-YYYY-NNNNNN` vía secuencia y es idempotente.
  - Deps: T4. Scope: M.

### Checkpoint B (T4–T5)
- [x] Estado de conversación sobrevive un reinicio del proceso.
- Hecho: test con dos `Session` distintas sobre Postgres real (commit + lectura posterior); 95 tests verdes.

## Fase 3 — Extracción

- [x] **T6. Extractor de texto (Gemini)**
  - [x] `ExtractorPort` + adaptador `google-genai` (`GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.5-flash`).
  - [x] Salida JSON con `response_mime_type` + schema; validación Pydantic; enum inválido → `null`.
  - [x] Redacción de teléfono, CBU/alias y nombres antes de enviar al LLM.
  - [x] Retry con backoff para 429 del free tier.
  - [x] Umbral de confianza 0.7 aplicado en el caso de uso.
  - Verificación: fixtures de respuestas válidas, inválidas y con campos inventados; test de redacción.
  - Hecho: `ports/extractor.py`, `domain/extraction.py` (`prefill` con umbral 0.7), `domain/redaction.py`, `adapters/gemini.py` (backoff 2s/4s, 3 intentos). 18 tests nuevos (113 verdes).
  - Riesgo: sin `GEMINI_API_KEY` en este host, la conversión de `response_schema` a la API real no está verificada; solo con cliente falso.
  - Deps: T2. Scope: M.

- [x] **T7. Extracción de visión (Gemini)**
  - [x] Prompt para comprobantes: monto, fecha, banco, alias/CBU, operación.
  - [x] Mismo modelo con visión; timeout y fallo → denuncia sigue sin `evidence_summary`.
  - Verificación: fixture de imagen de transferencia y caso de error del proveedor.
  - Hecho: `WireReceipt`/`ReceiptExtraction`/`to_receipt`; `ReceiptExtractorPort`; `extract_receipt` con timeout (`GEMINI_VISION_TIMEOUT_S=20`) y contrato "nunca lanza, devuelve `None`". 6 tests (119 verdes).
  - Deps: T6. Scope: S.

## Fase 4 — WhatsApp

- [x] **T8. Adaptador Neonize**
  - [x] Vinculación por QR y reconexión con sesión persistida.
  - [x] Recepción de texto e imagen; envío de texto; descarga de media.
  - [x] Implementa `MessagingPort`; sin lógica de negocio.
  - Verificación: smoke test manual con número real + contract test con transporte falso.
  - Hecho: `ports/messaging.py` (`InboundMessage`, `InboundMedia`, `MessagingPort`); `adapters/neonize.py` (`to_inbound`, `NeonizeMessaging`) con hilo propio, puente a asyncio por cola acotada, QR opcional, descarga con tope `WHATSAPP_MAX_MEDIA_BYTES` (10 MB) y degradación a texto si falla. 13 tests (133 verdes).
  - Nota: `connect()` de Neonize 0.5.2 no retorna tras `disconnect()`+`stop()`; el hilo es daemon y el cierre del proceso libera el socket. `stop()` es best-effort y avisa por log.
  - Pendiente: smoke test manual con número real (`scripts/whatsapp_smoke.py`).
  - Deps: T1. Scope: M.

- [x] **T9. Almacenamiento de evidencia**
  - [x] Guarda en `data/evidence/<report_id>/<sha256>.<ext>`.
  - [x] Dedupe por hash, máximo 5 archivos, 10 MB, jpeg/png/webp/pdf.
  - Verificación: tests de límite, mime no permitido, duplicado y hash correcto.
  - Hecho: `ports/evidence.py` (`EvidenceStorePort`, `StoredEvidence`, errores tipados); `adapters/evidence.py` (`FilesystemEvidenceStore`) con escritura atómica, ruta relativa al root, verificación de hash al deduplicar y rechazo de `report_id` no-UUID. 9 tests (142 verdes).
  - Nota: el tope de 5 archivos ya lo impone `Survey.add_evidence` (T3); el store no lo duplica.
  - Deps: T4. Scope: S.

### Checkpoint C (T6–T9)
- [ ] Un mensaje real con foto crea borrador y guarda evidencia.

## Fase 5 — Flujo completo

- [x] **T10. Ingesta**
  - [x] Primer contacto: aviso de privacidad + disclaimer, sin preguntas.
  - [x] Primer mensaje: extracción, borrador y primera pregunta faltante.
  - Verificación: integración con transporte y LLM falsos; solo pregunta lo faltante.
  - Hecho: `domain/messages.py` (textos, opciones numeradas, `question`, `choice`, `summary`);
    `services/conversation.py` (`ConversationService.handle`) con idempotencia por `wa_message_id`,
    guardado de evidencia, extracción con prefill, `llm_runs` y primera pregunta.
  - Desvío deliberado: si el primer mensaje trae contenido (no es solo saludo), se responde
    bienvenida + primera pregunta en el mismo turno para no perder el relato.
  - Nota: las opciones numeradas se traducen a etiqueta en `messages.choice`; `catalog.match_option`
    solo entiende etiquetas. Provincia se pide libre primero y con lista completa en el reintento.
  - Deps: T3, T5, T6, T9. Scope: M.

- [x] **T11. Confirmación y alta**
  - [x] Resumen numerado; `confirmar` / `corregir <campo>`.
  - [x] Alta con código público y disclaimer final.
  - Verificación: conversación completa termina en `submitted` con código único.
  - Hecho: `Survey.restore` reconstruye la encuesta desde la fila; migración `0002` agrega
    `conversations.evidence_skipped` y `omitted_fields` para que el avance sobreviva entre mensajes;
    `ReportRepository.to_draft` relee el borrador y cuenta evidencia.
  - 15 tests en `tests/test_conversation.py` (158 verdes).
  - Deps: T10. Scope: M.

- [x] **T12. Comandos y abandono**
  - [x] `ayuda`, `estado`, `cancelar`, `reiniciar`.
  - [x] Rate limit 20/min por JID; borrador `abandoned` a las 72 h.
  - Verificación: tests de cada comando, límite y job de abandono.
  - Hecho: `domain/commands.py` (`parse`, sin acentos y con `/ayuda`); comandos atendidos en
    cualquier estado antes de la encuesta; `cancelar`/`reiniciar` marcan el borrador `abandoned`
    y liberan la conversación; rate limit contado sobre `messages` (20/min por JID, se descarta
    en silencio y se loguea); `services/maintenance.py::abandon_stale` + `scripts/abandon_drafts.py`.
  - 8 tests de comandos + 3 de mantenimiento (169 verdes).
  - Deps: T10. Scope: S.

### Checkpoint D (T10–T12)
- [x] E2E de punta a punta contra dobles de test (`test_full_conversation_submits_and_returns_code`).
- [ ] Revisión humana del guion conversacional.

## Fase 6 — Salida a SICAR-C

- [ ] **T13. API de lectura y CSV**
  - [ ] `GET /api/v1/reports` paginado con filtros.
  - [ ] `GET /api/v1/reports/{id}` + evidencia.
  - [ ] CSV UTF-8 BOM, separador `;`, fechas ISO.
  - [ ] Bearer token + allowlist `SICAR_ALLOWED_IPS` (IP no permitida → 403).
  - Verificación: token requerido, IP fuera de rango rechazada, CSV abre en Excel es-AR.
  - Deps: T5, T11. Scope: M.

- [ ] **T14. Marcar exportada y contrato**
  - [ ] `POST /mark-exported` idempotente.
  - [ ] OpenAPI en `/docs` (apagado en prod).
  - Verificación: test de contrato y de doble marcado.
  - Deps: T13. Scope: S.

## Fase 7 — Endurecimiento y despliegue

- [ ] **T15. Logs y readiness**
  - [ ] Logs sin cuerpos de mensajes ni datos personales.
  - [ ] `/ready` chequea DB y conexión de WhatsApp.
  - [ ] Retención indefinida: sin job de purga (`RETENTION_DAYS=0`).
  - Verificación: test que falla si aparece texto de mensaje en logs.
  - Deps: T13. Scope: S.

- [ ] **T16. Docker y runbook**
  - [ ] Imagen con FFmpeg y volumen de sesión.
  - [ ] Runbook: QR, backup/restore, rotación de token y allowlist, re-vinculación.
  - Verificación: `docker compose up` levanta todo y el bot responde.
  - Deps: todas. Scope: M.

### Checkpoint Final
- [ ] `docker compose up` + denuncia completa + CSV abierto en Excel.
- [ ] Runbook probado por alguien que no escribió el código.

## Orden de ejecución

T1 → T2 → T3 → (A) → T4 → T5 → (B) → T6 → T7 → T8 → T9 → (C) → T10 → T11 → T12 → (D) → T13 → T14 → T15 → T16 → Final.

## Estimación

| Tramo | Horas |
|---|---:|
| T1–T3 (dominio) | 4–6 |
| T4–T9 (infra + canales) | 11–13 |
| T10–T12 (flujo) | 5–7 |
| T13–T16 (salida + deploy) | 7–9 |
| **Total** | **27–35 h** |

MVP usable (hasta Checkpoint D): 19–25 h.
