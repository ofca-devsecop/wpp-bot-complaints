# Implementation Plan: Bot de WhatsApp para denuncias SICAR-C

## Overview

Bot de WhatsApp que recibe denuncias de ciberdelitos en lenguaje libre (texto e imágenes) y completa los campos faltantes mediante una encuesta conversacional guiada. Al confirmar, registra la denuncia con un código público y la deja disponible para SICAR-C vía API JSON y export CSV.

Proyecto independiente. Sin relación con `sistemagestionmutualesbackend` ni con el frontend de mutuales. Vive en `sicar-bot/` con su propia base de datos.

## Scope

**Incluido en el MVP**
- Vinculación por QR con Neonize (API no oficial).
- Recepción de texto e imágenes.
- Extracción de campos con LLM (texto + visión de comprobantes).
- Encuesta determinística solo por los campos faltantes.
- Confirmación, corrección y alta de la denuncia.
- Evidencia almacenada con hash y metadatos.
- API de lectura + export CSV para SICAR-C.
- Docker Compose (app + Postgres) y runbook.

**Fuera del MVP**
- WhatsApp Flows (solo existe en la API oficial).
- Notificaciones proactivas o recordatorios.
- Backoffice web.
- Firma digital, validación de identidad, integración judicial.
- Múltiples números o multi-tenant.

## Architecture Decisions

| Decisión | Elección | Motivo |
|---|---|---|
| Canal | Neonize (whatsmeow) | QR sin cuenta Meta. Único costo: riesgo de baneo y sin Flows. |
| Encuesta | Máquina de estados determinística | Datos auditables, sin alucinaciones, testeable. |
| Rol del LLM | Extraer campos del mensaje inicial + analizar imágenes | El código calcula el complemento contra el catálogo. El LLM nunca decide el flujo. |
| UI del cuestionario | Texto con opciones numeradas | Los botones/listas no oficiales fallan seguido. Botones quedan detrás de un flag. |
| App DB | PostgreSQL + SQLAlchemy + Alembic | Reportes, conversaciones y evidencia. |
| Sesión WhatsApp | SQLite en volumen (`/data/neonize.db`) | Aísla el esquema de whatsmeow de la DB de negocio. |
| LLM | Gemini free tier vía `google-genai`, `gemini-3.5-flash` | Decisión del usuario. JSON con schema nativo y visión en el mismo modelo. Se redactan datos personales antes de enviar. |
| Retención | Indefinida, configurable (`RETENTION_DAYS=0`) | Decisión del usuario. Se documenta el riesgo legal. |
| Proceso | Un solo proceso: FastAPI + cliente Neonize en `lifespan` | Simple. Un deploy corta WhatsApp unos segundos; aceptable para MVP. |
| Idiomas | Español es-AR, timestamps `America/Argentina/Buenos_Aires` | Contexto del formulario. |
| API SICAR-C | Bearer token estático + lectura paginada + CSV | Simple y suficiente. Sin escritura remota. |

### Capas y puertos

```
domain/        enums, catálogos, validación, máquina de estados (puro, sin I/O)
app/           casos de uso: ingesta, encuesta, confirmación, exportación
ports/         MessagingPort, ExtractorPort, ReportRepository, EvidenceStore
adapters/      Neonize, LLM OpenAI-compatible, SQLAlchemy, filesystem
api/           FastAPI (lectura + CSV)
main.py        wiring y lifespan
```

`domain/` no importa nada de `adapters/`. Toda la lógica conversacional se testea sin WhatsApp ni LLM.

## Modelo de datos

### `reports`
| Campo | Tipo | Regla |
|---|---|---|
| `id` | UUID | PK |
| `public_code` | text | `SICAR-YYYY-NNNNNN`, único, generado al enviar |
| `wa_jid` | text | teléfono del denunciante (único dato de contacto; no se pide nombre ni DNI) |
| `status` | enum | `draft`, `submitted`, `exported`, `archived`, `abandoned` |
| `threat_type` | enum | ver catálogo |
| `severity` | enum | `alta`, `media`, `baja` |
| `province` | enum | 24 jurisdicciones |
| `locality` | text null | libre |
| `attack_medium` | enum null | ver catálogo |
| `had_economic_loss` | bool null | sí/no |
| `description` | text | relato del hecho |
| `evidence_summary` | text null | resumen generado de comprobantes |
| `created_at` / `updated_at` / `submitted_at` | timestamptz | UTC |

### `evidence`
`id`, `report_id`, `kind` (`image`/`document`), `mime_type`, `sha256`, `storage_path`, `caption`, `vision_json`, `created_at`.
Máximo 5 archivos por denuncia, 10 MB cada uno. Formatos: jpeg, png, webp, pdf.

### `conversations`
`wa_jid` (PK), `report_id`, `state` (`awaiting_first`, `collecting`, `confirming`, `done`), `current_field`, `last_question_at`, `retry_count`, `last_activity_at`.

### `messages`
`wa_message_id` (PK), `wa_jid`, `direction`, `kind`, `text_redacted`, `created_at`.
Sirve para idempotencia: Neonize puede reentregar eventos.

### `llm_runs`
`id`, `report_id`, `prompt_version`, `model`, `input_hash`, `output_json`, `created_at`.
Solo el JSON parseado y validado, nunca el prompt con datos personales.

## Catálogos (desde el formulario SICAR-C)

**Tipo de amenaza:** Fraude en línea · Usurpación de identidad · Acceso ilegítimo · Phishing · Acoso · Otros

**Severidad:** Alta · Media · Baja

**Provincia (24):** Buenos Aires · Catamarca · Chaco · Chubut · Ciudad Autónoma de Buenos Aires · Córdoba · Corrientes · Entre Ríos · Formosa · Jujuy · La Pampa · La Rioja · Mendoza · Misiones · Neuquén · Río Negro · Salta · San Juan · San Luis · Santa Cruz · Santa Fe · Santiago del Estero · Tierra del Fuego · Tucumán

**Medio del atacante:** WhatsApp · SMS · Llamada telefónica · Telegram · Instagram · Facebook · Correo electrónico · Sitio web falso · Otro

**Pérdida económica:** Sí · No

### Normalización de provincia
Alias aceptados además del nombre exacto: `CABA`, `Capital Federal`, `Ciudad de Buenos Aires`, `Bs As`, `Baires`, `Tucuman`, `Cordoba`, `Entre Rios`, `Rio Negro`, `Neuquen`, `Chubut`, etc. Se normaliza sin acentos, minúsculas y se compara contra un mapa de alias. Ambigüedad → se repregunta.

## Flujo conversacional

1. **Primer contacto.** Saludo + aviso de privacidad + disclaimer. Estado `awaiting_first`. No se pregunta nada todavía.
2. **Primer mensaje con contenido.** Texto y/o imágenes. Se descargan las imágenes y se ejecuta extracción (texto + visión). Se crea `report` en `draft`.
3. **Cálculo de faltantes.** El LLM devuelve qué encontró y con qué confianza. El código compara contra los campos requeridos (`threat_type`, `severity`, `province`, `description`) y opcionales (`locality`, `attack_medium`, `had_economic_loss`). Un campo se prellena solo si la confianza ≥ 0.7 y el valor pasa la validación del catálogo.
4. **Encuesta.** Se pregunta un campo por vez, en este orden: tipo de amenaza → severidad → provincia → localidad → medio → pérdida económica → descripción (si falta) → evidencia (si no hay) . Respuesta inválida: se repregunta una vez con las opciones. Opcionales aceptan `omitir`.
5. **Confirmación.** Resumen numerado de todos los campos. El usuario responde `confirmar` o `corregir <campo>`.
6. **Alta.** Estado `submitted`, código público, respuesta con código + disclaimer. Estado `done`.
7. **Comandos en cualquier momento:** `ayuda`, `estado`, `cancelar`, `reiniciar`.
8. **Después de `done`.** Un mensaje nuevo abre una denuncia nueva (se avisa y se pide confirmación).

### Reglas de la máquina de estados
- Un campo requerido inválido no avanza el estado.
- `cancelar` pide confirmación y descarta el borrador.
- `reiniciar` descarta y vuelve a `awaiting_first`.
- Tras 72 h sin actividad, el borrador queda `abandoned` (job simple, no notifica).
- Rate limit: 20 mensajes por minuto por JID.

## Agrupación de mensajes consecutivos

WhatsApp entrega un mismo envío del usuario como varios mensajes: primero la foto y
después el texto que la explica, textos largos partidos en dos, o dos fotos seguidas. Hoy
cada mensaje es un turno completo de la encuesta: la foto sola dispara bienvenida + primera
pregunta, y el texto que llega después se interpreta como respuesta a esa pregunta.

**Decisión: ventana de agrupación por JID, solo alrededor de media sin texto.**

- Un mensaje que trae media y **no** trae texto útil (ni caption) no se procesa solo: queda
  pendiente para ese JID y abre una ventana de `MERGE_WINDOW_SECONDS` (3 s por defecto).
- Si llega otro mensaje del mismo JID dentro de la ventana, se fusionan en un único turno:
  textos concatenados con `\n` y media acumulada.
- Si la ventana vence sin otro mensaje, el pendiente se procesa solo. Una foto sola igual
  recibe respuesta.
- Un mensaje con texto se procesa de inmediato, salvo que haya media pendiente: en ese caso
  se fusiona y se procesa sin esperar el resto de la ventana.
- Los mensajes con texto **nunca** se agrupan entre sí. Dos respuestas seguidas (`alta`,
  `CABA`) siguen siendo dos turnos; fusionarlas rompería la encuesta.
- El pendiente vive en memoria. Si el proceso muere se pierde y el usuario reenvía.
- El rate limit cuenta turnos entregados, no mensajes sueltos: una ráfaga es un turno.
- Un turno con texto responde la encuesta y guarda su media como evidencia. En el paso
  de evidencia, si hay media se cuenta como evidencia y el texto se ignora.

### Cambios

1. `ports/messaging.py`: `InboundMessage.media` pasa a `medias: tuple[InboundMedia, ...]`.
2. `adapters/neonize.py`: `_extract_medias` devuelve una tupla (hoy 0 o 1 elemento).
3. `ports/extractor.py` y `adapters/gemini.py`: `extract(text, medias)` manda un
   `Part.from_bytes` por archivo.
4. `services/conversation.py`: guarda cada evidencia del turno y extrae con todas.
5. `services/merger.py` (nuevo): `MessageMerger(handler, window)`, un pendiente y un timer por
   JID, `aclose()` para cancelarlos.
6. Wiring: `messaging.on_message(merger)` en lugar de `service.handle`.
7. `config.py`: `merge_window_seconds: float = 3.0`.

### Riesgos

- Latencia de hasta 3 s solo cuando llega media sin texto.
- Foto y texto separados por más de la ventana son dos turnos; el texto cae en la primera
  pregunta y la encuesta repregunta. Aceptable.
- Un pendiente por JID como máximo; expira con la ventana.

## Contrato de extracción LLM

Salida JSON estricta, validada con Pydantic. Valor desconocido o enum inválido → `null`.

```json
{
  "threat_type": {"value": "fraude_en_linea", "confidence": 0.0},
  "severity": {"value": "media", "confidence": 0.0},
  "province": {"value": "cordoba", "confidence": 0.0},
  "locality": {"value": null, "confidence": 0.0},
  "attack_medium": {"value": "whatsapp", "confidence": 0.0},
  "had_economic_loss": {"value": true, "confidence": 0.0},
  "description": {"value": "texto", "confidence": 0.0},
  "evidence_summary": "transferencia de $150.000 del 12/10, alias ..."
}
```

**Visión de comprobantes** devuelve: monto, moneda, fecha, banco/billetera, alias/CBU/CVU, número de operación y una frase descriptiva. Si falla, la denuncia sigue sin ese dato; nunca bloquea el flujo.

## API para SICAR-C

| Método | Ruta | Uso |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/ready` | DB + cliente WhatsApp |
| GET | `/api/v1/reports` | listado paginado, filtros `status`, `since`, `province` |
| GET | `/api/v1/reports/{id}` | detalle + evidencias |
| GET | `/api/v1/reports/{id}/evidence/{evidence_id}` | stream del archivo |
| POST | `/api/v1/reports/{id}/mark-exported` | marca exportada |
| GET | `/api/v1/export.csv` | CSV completo o por rango |

- Auth: `Authorization: Bearer <SICAR_API_TOKEN>` (comparación en tiempo constante) + allowlist de IPs/CIDR en `SICAR_ALLOWED_IPS`. Sin IP permitida → 403 aunque el token sea válido.
- CSV: UTF-8 con BOM, separador `;`, fechas ISO con offset, campos escapados.
- OpenAPI en `/docs`, apagado en producción si `ENV=prod`.

## Textos legales y conversacionales (borrador)

> Pendiente de aprobación institucional. Viven en `domain/messages.py` para cambiarlos sin tocar lógica.

**Bienvenida y consentimiento**
```
Hola. Soy el asistente de SICAR-C para recibir denuncias de ciberdelitos.
Contame qué pasó con tus palabras y, si tenés, mandá capturas o comprobantes.
Te voy a hacer algunas preguntas para completar el registro.

Este registro no reemplaza una denuncia formal. Si estás en peligro, llamá al 911.

Al continuar aceptás el aviso de privacidad: tus datos se usan solo para
registrar y derivar tu denuncia a SICAR-C. Guardamos tu teléfono, el relato
y las imágenes que envíes. No se usan para publicidad. Responsable: OFCA.
Ley 25.326.
```

**Pedido de evidencia**
```
Si tenés capturas de la conversación, el comprobante de la transferencia o el
perfil del atacante, mandalas ahora. No compartas datos que no quieras registrar.
```

**Cierre**
```
Listo. Tu denuncia quedó registrada con el código {codigo}. Guardalo.
Un operador de SICAR-C la va a revisar.

Este registro no reemplaza una denuncia formal ante la justicia. Para denuncias
penales, acercate a la fiscalía o comisaría más cercana. Ante una emergencia,
llamá al 911.
```

**Cancelación**
```
Cancelé el registro y borré los datos del borrador. Si querés empezar de nuevo,
escribí HOLA.
```

## Task List

### Fase 0 — Scaffold
- [ ] T1. Estructura del repo, `pyproject.toml`, config por entorno, Docker Compose, `/health`.

### Fase 1 — Dominio puro
- [ ] T2. Catálogos, enums, normalización de provincias y validadores.
- [ ] T3. Máquina de estados de la encuesta con tests table-driven.

### Checkpoint A (tras T1–T3)
- [ ] `pytest` verde. El flujo se puede simular sin WhatsApp ni LLM.

### Fase 2 — Persistencia
- [ ] T4. Modelos SQLAlchemy + migración Alembic inicial.
- [ ] T5. Repositorios e idempotencia por `wa_message_id`.

### Fase 3 — Extracción
- [ ] T6. Puerto `ExtractorPort` + adaptador OpenAI-compatible + validación estricta.
- [ ] T7. Extracción de visión para comprobantes con degradación elegante.

### Fase 4 — WhatsApp
- [ ] T8. Adaptador Neonize: QR, recepción, envío, descarga de media.
- [ ] T9. Almacenamiento de evidencia: hash, dedupe, límites, tipos permitidos.

### Checkpoint B (tras T4–T9)
- [ ] Un mensaje real con foto crea un borrador con evidencia guardada.

### Fase 5 — Flujo completo
- [ ] T10. Ingesta: primer mensaje → extracción → borrador → primera pregunta faltante.
- [ ] T11. Confirmación, corrección, alta y código público.
- [ ] T12. Comandos, cancelación, rate limit y abandono.
- [ ] T17. Agrupación de mensajes consecutivos (ventana por JID, media + texto = un turno).

### Checkpoint C (tras T10–T12)
- [ ] Conversación completa de punta a punta contra transporte y LLM falsos.

### Fase 6 — Salida a SICAR-C
- [ ] T13. API de lectura con Bearer + CSV compatible con Excel es-AR.
- [ ] T14. `mark-exported`, OpenAPI y tests de contrato.

### Fase 7 — Endurecimiento y despliegue
- [ ] T15. Redacción de logs, límites y `/ready`.
- [ ] T16. Docker, runbook de QR, backup/restore y README.

### Checkpoint Final
- [ ] `docker compose up` levanta todo, el bot responde y el CSV se abre en Excel.

## Risks and Mitigations

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Baneo del número por API no oficial | Alto | Número dedicado, rate limit, sin envíos masivos, plan B: migrar a Cloud API (el dominio no cambia). |
| El LLM inventa datos | Alto | Esquema estricto, confianza mínima, validación contra catálogo y confirmación humana. |
| Pérdida de la sesión de WhatsApp | Medio | Volumen persistente + re-vinculación por QR documentada. |
| Datos personales y financieros | Alto | Token en API, logs sin cuerpos, disco cifrado, retención configurable, aviso de privacidad. |
| Reentrega de eventos | Medio | PK `wa_message_id` + operaciones idempotentes. |
| Botones no oficiales inestables | Medio | MVP con opciones numeradas; botones detrás de flag. |
| Un deploy corta WhatsApp | Bajo | Aceptado en MVP; separar worker y API si molesta. |
| Gemini free tier puede usar los prompts para mejorar el producto | Alto | Redactar teléfono, CBU/alias y nombres antes de enviar; habilitar facturación antes de usuarios reales. |
| Retención indefinida choca con el principio de finalidad de la Ley 25.326 | Medio | Dejarla configurable y apagada; revisar con legales antes de producción. |

## Resolved Decisions

- LLM: Gemini free tier, `gemini-3.5-flash`, vía `google-genai`.
- Catálogo: "Acceso ilegítimo".
- Denunciante: solo teléfono; sin nombre ni DNI.
- API: Bearer token + allowlist de IPs/CIDR.
- Retención: indefinida, configurable con `RETENTION_DAYS=0`.
- Legal: borrador generado en este plan, pendiente de aprobación institucional.

## Open Questions

1. ¿Se habilita facturación en Gemini antes de producción? El free tier puede usar los datos para mejorar el producto.
2. ¿Quién aprueba el texto legal y a qué URL apunta el aviso de privacidad?
3. ¿OFCA tiene un rango de IPs fijo para la allowlist?
