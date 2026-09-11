# Auditoría y evidencia de pruebas

Fecha: 11 de septiembre de 2026. Alcance: finalizar incrementalmente FieldScope según la solicitud de auditoría y el documento Customer Installed Base Intelligence proporcionados por el usuario. Se conservaron FastAPI, SQLite, JavaScript vanilla y QVAC 0.18.1.

## Resultado

El flujo textual completo pasó en Edge con Qwen real: captura, dos preguntas, confirmación, SQLite, Cliente 360, Geografía, Panorama, consulta y eliminación de una observación conservando equipos. También pasaron dos dictados consecutivos con micrófono sintético y Whisper real, cancelación y revisión móvil. La suite determinística final tiene **74 pruebas aprobadas**. Esto acredita un prototipo probado localmente; no certifica un despliegue empresarial.

## Hallazgos priorizados y correcciones

| Prioridad | Problema observado | Corrección y evidencia |
| --- | --- | --- |
| HIGH | El entorno existente apuntaba a un Python inexistente y `python` podía resolver a MSYS. | Entorno separado `.venv` con CPython 3.12.14; se preservó el anterior. Instalación, pytest y backend reales ejecutados. |
| HIGH | El SDK no encontraba de forma fiable el worker/Bare instalado por npm; el timeout de conexión no tomaba el valor esperado. | Descubrimiento del worker local y Bare hoisted; timeout explícito en `Client.connect`. Qwen y Whisper cargaron realmente. |
| HIGH | Campos inventados o trasladados entre equipos; fabricante copiado como modelo; cantidad desconocida convertida en 1. | Grounding por evidencia del equipo, normalización común y null explícito. Regresiones de modalidades, rangos, números escritos y contexto. |
| HIGH | El contexto acumulado podía servir como falsa evidencia para un campo nuevo de otro equipo. | Evidencia limitada a la fila saneada correspondiente y al campo preguntado; pruebas de aislamiento entre filas. |
| HIGH | Seguimiento duplicado, pérdida del texto o sesión ante errores y controles de confirmación inconsistentes. | Flujo acotado, pregunta individual, conservación de texto/sesión y confirmación explícita. API y navegador completos. |
| HIGH | Consolidación demasiado amplia, confianza por repetir con el mismo reportero y falta de enlaces a todos los equipos. | Matching conservador, reporteros distintos para corroboración y tabla ObservationEquipment. Pruebas de conflictos y borrado. |
| HIGH | Voz con riesgos de doble inicio, respuestas tardías y recursos abiertos. | Generaciones de captura, estados ocupados, cancelación, límites, abort y liberación de tracks; WAV validado y eliminado. Dos dictados reales mediante dispositivo sintético. |
| HIGH | Inferencias simultáneas y errores/timeout del worker podían dejar estado de disponibilidad engañoso. | Serialización, cola acotada, cierre limitado y limpieza de readiness. Tests de concurrencia, desconexión y cancelación de inicio. |
| MEDIUM | Una salida JSON válida seguida de otro objeto activaba reparación innecesaria. | `JSONDecoder.raw_decode` lee un objeto completo sin combinar objetos ni rescatar hijos de JSON malformado. Cinco regresiones Qwen reales pasaron. |
| MEDIUM | Fechas históricas faltantes podían parecer recientes después de una migración. | No fabricar fechas de verificación; migración idempotente probada con esquema heredado. Fechas API con UTC. |
| MEDIUM | Catálogo geográfico duplicado y variantes con tildes producían navegación inconsistente. | Catálogo compartido JSON, aliases y normalización de países; se conserva la ruta de navegación al refrescar. |
| MEDIUM | Dashboard confundía categorías con unidades y excluía edades fraccionarias; no representaba cantidad desconocida. | Sumas de cantidades conocidas, conteo separado de desconocidas, buckets continuos, completitud y freshness. |
| MEDIUM | Consultas con criterios no reconocidos podían devolver un conjunto demasiado amplio. | Gramática explícita que rechaza filtros desconocidos, negaciones, rangos y combinaciones no soportadas; sin SQL generado por IA. |
| MEDIUM | Documentación de voz y estado QVAC no coincidía con el código. | README reescrito con funcionalidades, evidencia, comandos y límites reales; guía Windows alineada. |
| LOW | CSS anulaba el atributo hidden y el botón enviar saltaba de fila en móvil. | Regla hidden, layout del compositor y comprobaciones geométricas en Edge a 390 px. |
| LOW | Copias antiguas, base SQLite y artefactos privados no debían publicarse. | Exclusiones Git y retirada del índice conservando archivos locales; sin reescribir historial. |

No se atribuyó artificialmente una categoría CRITICAL a un hallazgo sin evidencia. Se revisaron Python, HTML/CSS/JS, dependencias, prompts, configuración, esquemas, rutas, scripts, documentación y artefactos locales.

## Pruebas ejecutadas

| Comprobación | Resultado y alcance |
| --- | --- |
| `python -m pytest -q` | **74 passed**, una advertencia de deprecación interna de Starlette/AnyIO; no se ocultó. |
| `python backend/mvp_smoke_test.py` | Aprobada: captura, persistencia, consulta y oportunidad. Usa parser determinístico, no QVAC real. |
| `python backend/qvac_warmup.py` | Aprobada con Qwen real y validación de campos del ejemplo MR/CT. |
| `python backend/qvac_verify.py` | Cinco escenarios Qwen reales aprobados: CT sin modelo, CT con Incisive, tomógrafo, rayos X y seguimiento de fabricante. |
| `QVAC_REAL=1 node tests/browser-smoke.cjs` | Aprobada en Edge: interfaz y API reales, SQLite aislado, Qwen y Whisper. Sin errores de consola/página ni solicitudes remotas del navegador. |
| Dos dictados en Edge | Respuestas 200 con texto; entrada y botón reutilizables. Micrófono falso con WAV sintético; nunca se capturó el micrófono del usuario. |
| Voice warmup con WAV completo | Aprobado al repetir tras un fallo de conexión; ver observaciones abajo. |
| Inspección visual | Captura desktop/mobile, Cliente 360 y Panorama. Corregidos controles ocultos y compositor móvil; sin overflow horizontal en 390 px. |
| Integridad SQLite existente | `PRAGMA integrity_check`: ok en ambas bases. Lectura únicamente; no se publicaron filas. |
| Dependencias | `pip check` sin conflictos; `pip-audit -l` sin vulnerabilidades conocidas después de actualizar pip y python-dotenv. npm audit en la raíz y en backend: 0 vulnerabilidades conocidas. |

Las pruebas de pytest usan SQLite temporal y QVAC desactivado de forma explícita. Cubren contrato API, validación, anti-alucinación, deduplicación, estados, freshness, seed, migraciones, filtros con entrada SQL maliciosa, tamaños de payload y errores de audio/runtime. La prueba de navegador crea una base bajo `test-results/`, rechaza servidores preexistentes y detiene solo los procesos que inició.

## Evidencia QVAC y voz

En la última prueba completa de Edge antes de la revisión final:

```json
{
  "qvac_ready": true,
  "extraction_mode": "qvac_on_device",
  "last_inference_note": "qvac_local",
  "qvac_asr_ready": true,
  "voice_transcription": "qvac_on_device",
  "cloud_inference": false,
  "last_inference_ms": 5137,
  "last_voice_inference_ms": 810
}
```

Son mediciones puntuales, no benchmarks. El log verificó apagado completo del backend. Los screenshots, logs y JSON de QA se mantienen ignorados bajo `test-results/`.

El WAV sintético completo decía: «Visité Hospital Demo Aurora. Tienen dos equipos de resonancia de ocho años». En la comprobación directa, Whisper devolvió: «Visiteos hospital de Moorora. Tienen dos equipos de Resonancia de 8 años». Reconoció modalidad, cantidad y edad, pero **no el nombre del hospital**. Por eso el producto permite revisar y corregir la transcripción antes de enviar. No se afirma exactitud universal de voz ni se sustituyó Whisper por un servicio remoto.

Una ejecución adicional del warmup de voz falló durante la conexión RPC con `WinError 64` / `CancelledError`. La misma comprobación volvió a pasar al iniciar un worker nuevo; no se estableció la causa del corte de conexión de Windows. Se acotó el cierre y se preservó la cancelación/timeout para diagnóstico, sin modificar ni silenciar internamente el SDK. Ante una desconexión persistente se requiere reiniciar el backend y diagnosticar el entorno.

El primer intento dentro del sandbox también encontró límites de arranque del worker; las pruebas reales se ejecutaron fuera de ese aislamiento. Un fallo inicial del harness por `fetch` interno de Node se resolvió usando `node:http`. Un fixture de voz mal codificado en PowerShell 5 se corrigió conservando UTF-8 con BOM. No se retiraron tests fallidos para obtener resultados verdes.

## Datos y revisión de seguridad

La base heredada `data/installed_base.db` estaba vacía. La base activa local `backend/data/installed_base.db` contenía 2 clientes, 4 grupos de equipos y 2 observaciones. Ambas se conservaron y se inspeccionaron en modo de solo lectura. Las migraciones se probaron en bases aisladas; los registros históricos no se reescribieron mediante IA.

La aplicación limita host/origin local, serializa escrituras, usa consultas parametrizadas y escapa contenido en la UI. Texto: 4000 caracteres; consulta: 500; payload JSON: 32 KiB; audio: 2 MiB y 60 segundos. No se añadió ninguna API de IA externa. El seed ficticio es atómico y opt-in. Modelos, SQLite, grabaciones, entornos, node_modules, `.env`, logs y backups no deben formar parte del commit.

Limitaciones de seguridad: no hay autenticación, cifrado propio de SQLite, RBAC ni auditoría empresarial inmutable. Los nombres de reportero no prueban identidad. La revisión de secretos y dependencias reduce riesgos observables, pero no equivale a una certificación de seguridad.

## Límites de la validación

- No se desconectó físicamente la red del sistema operativo. Se verificaron modelos cacheados y ausencia de llamadas externas del navegador; no se realizó una auditoría de paquetes de red del worker.
- No se validó micrófono físico, ruido real ni todos los navegadores/sistemas operativos.
- Visión/fotos, sincronización multiusuario, generalización de consultas y edición estructurada campo a campo siguen pendientes, declarados en README.
- El agrupamiento puede conservar duplicados ambiguos y no dispone de resolución manual de conflictos.
- Al borrar una observación se conserva el snapshot y los contadores acumulados; no se revierte el historial de consolidación.
- Bases con datos antiguos se conservan, pero la nueva evidencia no sanea retroactivamente afirmaciones históricas.

Revisión final de staging: sin coincidencias de patrones de credenciales y sin bases, modelos, cachés ni entornos versionados. Los documentos duplicados y la base heredada se retiraron únicamente del índice; sus copias locales se conservaron. El historial Git previo no se reescribió.

Los comandos de instalación, demo y reproducción de pruebas están centralizados en [README](../README.md).
