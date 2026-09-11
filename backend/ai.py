from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import time
import unicodedata
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import (
    QVAC_MODEL,
    QVAC_CTX_SIZE,
    QVAC_CACHE_DIR,
    QVAC_REQUIRED,
)
from prompts import EXTRACTION_PROMPT

try:
    from tetherto.qvac_sdk import (
        Client,
        TranscribeRequest,
        completion,
        load_model,
        transcribe,
        unload_model,
    )
    import tetherto.qvac_sdk.models as qvac_models
except Exception:
    Client = None
    TranscribeRequest = None
    completion = None
    load_model = None
    transcribe = None
    unload_model = None
    qvac_models = None

from normalization import (
    ground_extraction as sanitize_extraction, normalize_modality, clean,
    MODALITY_ALIASES, NUMBER_WORDS, equipment_mentions, explicit_age,
    normalize_country, country_aliases,
)
from config import (QVAC_ASR_MODEL, QVAC_INFERENCE_TIMEOUT_SECONDS,
                    QVAC_STARTUP_TIMEOUT_SECONDS, QVAC_ENABLED)

class QvacNotReadyError(RuntimeError):
    """La inferencia QVAC local no está disponible."""


_qvac_client = None
_qvac_transport = None
_qvac_model_id: Optional[str] = None
_qvac_start_error: Optional[str] = "not_initialized"
_qvac_ready = False
_last_inference_ms: Optional[int] = None
_last_inference_note: Optional[str] = None

# ASR local (voz -> texto) cargado de forma perezosa para no afectar
# el arranque normal del LLM. La primera carga puede descargar el modelo;
# luego QVAC lo reutiliza desde el mismo cache local.
_qvac_asr_model_id: Optional[str] = None
_qvac_asr_start_error: Optional[str] = "not_initialized"
_qvac_asr_lock = None
_last_voice_inference_ms: Optional[int] = None


def _print_progress(progress) -> None:
    try:
        downloaded_mb = progress.downloaded / 1e6
        total_mb = progress.total / 1e6
        percentage = progress.percentage
        print(
            f"\r[QVAC] Modelo {percentage:.0f}% "
            f"({downloaded_mb:.1f}/{total_mb:.1f} MB)",
            end="",
            flush=True,
        )
        if percentage >= 100:
            print()
    except Exception:
        pass


async def initialize_qvac() -> None:
    """
    Inicializa el worker QVAC y carga el modelo UNA sola vez.

    El modelo se descarga/cachea durante la preparación del equipo.
    Una vez presente en el cache, las inferencias posteriores son locales.
    """
    global _qvac_client
    global _qvac_transport
    global _qvac_model_id
    global _qvac_start_error
    global _qvac_ready

    if _qvac_ready:
        return
    if not QVAC_ENABLED:
        raise QvacNotReadyError("QVAC disabled by configuration")

    _qvac_start_error = None

    if Client is None or qvac_models is None:
        _qvac_start_error = (
            "QVAC Python SDK no está instalado. "
            "Instala tetherto-qvac-sdk."
        )
        raise QvacNotReadyError(_qvac_start_error)

    model_src = getattr(qvac_models, QVAC_MODEL, None)
    if model_src is None:
        _qvac_start_error = (
            f"El modelo QVAC '{QVAC_MODEL}' no existe en esta versión del SDK."
        )
        raise QvacNotReadyError(_qvac_start_error)

    Path(QVAC_CACHE_DIR).mkdir(parents=True, exist_ok=True)

    try:
        local_sdk = Path(__file__).parent / "node_modules" / "@qvac" / "sdk"
        sdk_dir = os.getenv("QVAC_SDK_DIR") or (str(local_sdk) if local_sdk.exists() else None)
        # npm may hoist Bare next to @qvac rather than inside @qvac/sdk.
        platform_name = {"Windows": "win32", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
        architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
        hoisted_bare = Path(__file__).parent / "node_modules" / f"bare-runtime-{platform_name}-{architecture}" / "bin" / ("bare.exe" if os.name == "nt" else "bare")
        bare_path = os.getenv("QVAC_BARE_PATH") or (str(hoisted_bare) if hoisted_bare.is_file() else None)
        _qvac_client = Client(
            sdk_dir=sdk_dir,
            bare_path=bare_path,
            config={
                "cacheDirectory": str(Path(QVAC_CACHE_DIR).resolve()),
                "loggerLevel": "warn",
                "loggerConsoleOutput": False,
            }
        )

        await _qvac_client.connect(timeout=int(os.getenv("QVAC_RPC_INIT_TIMEOUT_MS", "120000")) / 1000)
        _qvac_transport = _qvac_client.transport

        print(
            f"[QVAC] Cargando {QVAC_MODEL} en el dispositivo "
            f"(ctx={QVAC_CTX_SIZE})..."
        )

        _qvac_model_id = await load_model(
            _qvac_transport,
            model_src=model_src,
            model_config={
                "ctx_size": QVAC_CTX_SIZE,
            },
            on_progress=_print_progress,
        )

        _qvac_ready = True
        _qvac_start_error = None

        print(
            "[QVAC] Listo. Inferencia local habilitada; "
            "no se usa ninguna API de IA en la nube."
        )

    except (Exception, asyncio.CancelledError) as exc:
        _qvac_ready = False
        _qvac_model_id = None
        _qvac_start_error = str(exc) or type(exc).__name__

        if _qvac_client is not None:
            try:
                await asyncio.wait_for(_qvac_client.__aexit__(None, None, None), timeout=5)
            except Exception:
                pass

        _qvac_client = None
        _qvac_transport = None

        if isinstance(exc, asyncio.CancelledError):
            raise

        raise QvacNotReadyError(
            f"No se pudo iniciar QVAC: {_qvac_start_error}"
        ) from exc


async def initialize_qvac_asr() -> None:
    """Carga WHISPER_TINY en el mismo worker QVAC, solo cuando se necesita voz."""
    global _qvac_asr_model_id
    global _qvac_asr_start_error
    global _qvac_asr_lock

    if _qvac_asr_model_id is not None:
        return

    if (
        not _qvac_ready
        or _qvac_transport is None
        or qvac_models is None
        or load_model is None
        or TranscribeRequest is None
        or transcribe is None
    ):
        _qvac_asr_start_error = (
            "QVAC no está listo para transcripción local. "
            "Inicia primero el backend con QVAC disponible."
        )
        raise QvacNotReadyError(_qvac_asr_start_error)

    model_src = getattr(qvac_models, QVAC_ASR_MODEL, None)
    if model_src is None:
        _qvac_asr_start_error = (
            f"El modelo ASR QVAC '{QVAC_ASR_MODEL}' no existe en esta versión del SDK."
        )
        raise QvacNotReadyError(_qvac_asr_start_error)

    if _qvac_asr_lock is None:
        _qvac_asr_lock = asyncio.Lock()

    async with _qvac_asr_lock:
        # Otro request pudo terminar de cargarlo mientras esperábamos el lock.
        if _qvac_asr_model_id is not None:
            return

        _qvac_asr_start_error = None

        try:
            print(
                f"[QVAC-ASR] Cargando {QVAC_ASR_MODEL} para voz local "
                "(16 kHz mono, español)..."
            )

            _qvac_asr_model_id = await load_model(
                _qvac_transport,
                model_src=model_src,
                model_config={
                    "audio_format": "f32le",
                    "language": "es",
                },
                on_progress=_print_progress,
            )

            _qvac_asr_start_error = None
            print(
                "[QVAC-ASR] Listo. Dictado local habilitado; "
                "el audio no se envía a una API de voz en la nube."
            )

        except Exception as exc:
            _qvac_asr_model_id = None
            _qvac_asr_start_error = str(exc)
            raise QvacNotReadyError(
                f"No se pudo iniciar QVAC ASR ({QVAC_ASR_MODEL}): {exc}"
            ) from exc


async def transcribe_audio_file(audio_path: str) -> str:
    """Transcribe un WAV 16 kHz mono mediante QVAC ASR on-device."""
    global _last_voice_inference_ms

    await initialize_qvac_asr()

    if (
        _qvac_transport is None
        or _qvac_asr_model_id is None
        or TranscribeRequest is None
        or transcribe is None
    ):
        raise QvacNotReadyError("QVAC ASR todavía no está listo.")

    path = Path(audio_path).resolve()
    if not path.exists():
        raise QvacNotReadyError("No se encontró el audio temporal")

    request = TranscribeRequest.model_validate(
        {
            "type": "transcribe",
            "modelId": _qvac_asr_model_id,
            "audioChunk": {
                "type": "filePath",
                "value": str(path),
            },
            "metadata": True,
        }
    )

    started = time.perf_counter()
    segments = []

    try:
        async for response in transcribe(_qvac_transport, request):
            if response.segment is not None:
                segments.append(response.segment)
    except Exception as exc:
        _last_voice_inference_ms = int(
            (time.perf_counter() - started) * 1000
        )
        await _shutdown_unlocked()
        raise QvacNotReadyError(
            f"La transcripción QVAC local falló: {exc}"
        ) from exc

    _last_voice_inference_ms = int(
        (time.perf_counter() - started) * 1000
    )

    transcript = "".join(segment.text for segment in segments).strip()
    if not transcript:
        raise QvacNotReadyError(
            "QVAC ASR no detectó voz comprensible en la grabación."
        )

    return transcript


async def shutdown_qvac() -> None:
    """Libera los modelos de texto/voz y cierra el worker local de QVAC."""
    global _qvac_client
    global _qvac_transport
    global _qvac_model_id
    global _qvac_asr_model_id
    global _qvac_ready

    # Descargar primero ASR si fue usado durante esta sesión.
    if _qvac_transport is not None and _qvac_asr_model_id is not None:
        try:
            await asyncio.wait_for(unload_model(
                _qvac_transport,
                _qvac_asr_model_id,
            ), timeout=3)
        except Exception as exc:
            print(f"[QVAC-ASR] Advertencia al descargar modelo: {exc}")

    if _qvac_transport is not None and _qvac_model_id is not None:
        try:
            await asyncio.wait_for(unload_model(
                _qvac_transport,
                _qvac_model_id,
            ), timeout=3)
        except Exception as exc:
            print(f"[QVAC] Advertencia al descargar modelo: {exc}")

    if _qvac_client is not None:
        try:
            await asyncio.wait_for(_qvac_client.__aexit__(None, None, None), timeout=5)
        except Exception as exc:
            print(f"[QVAC] Advertencia al cerrar worker: {exc}")

    _qvac_client = None
    _qvac_transport = None
    _qvac_model_id = None
    _qvac_asr_model_id = None
    _qvac_ready = False


def ai_is_configured() -> bool:
    return Client is not None and qvac_models is not None


def get_ai_runtime_status() -> Dict[str, Any]:
    """
    Estado verificable del motor para la demo.

    cloud_inference=False es intencional: este proyecto no envía
    observaciones a proveedores externos para inferencia.
    """
    return {
        "ai_configured": ai_is_configured(),
        "ai_available": _qvac_ready,
        "qvac_ready": _qvac_ready,
        "inference_provider": "QVAC",
        "inference_location": "on_device",
        "extraction_mode": "qvac_on_device" if _qvac_ready else ("qvac_not_ready" if QVAC_REQUIRED else "deterministic_dev_fallback"),
        "cloud_inference": False,
        "network_required_for_inference": False,
        "model": QVAC_MODEL,
        "cache_present": Path(QVAC_CACHE_DIR).is_dir(),
        "qvac_required": QVAC_REQUIRED,
        "qvac_enabled": QVAC_ENABLED,
        "startup_error": None if _qvac_ready else ("QVAC disabled by configuration" if not QVAC_ENABLED else "QVAC unavailable; run qvac_warmup.py for local diagnostics"),
        "last_inference_ms": _last_inference_ms,
        "last_inference_note": _last_inference_note,
        "qvac_asr_model": QVAC_ASR_MODEL,
        "qvac_asr_ready": _qvac_asr_model_id is not None,
        "qvac_asr_start_error": "ASR initialization failed; check local diagnostics" if _qvac_asr_start_error not in (None, "not_initialized") else _qvac_asr_start_error,
        "voice_transcription": (
            "qvac_on_device"
            if _qvac_asr_model_id is not None
            else "lazy_not_loaded"
        ),
        "last_voice_inference_ms": _last_voice_inference_ms,
    }


def _empty_result() -> Dict[str, Any]:
    return {
        "customer_name": "Desconocido",
        "city": None,
        "country": None,
        "equipment": [],
    }


def _normalize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza la forma consumida por el resto del backend."""
    if not isinstance(data, dict):
        return _empty_result()

    customer_name = data.get("customer_name") or "Desconocido"
    equipment = data.get("equipment") or []
    normalized_equipment = []

    for eq in equipment:
        if not isinstance(eq, dict):
            continue

        modality = eq.get("modality")
        if not modality:
            continue

        quantity = eq.get("quantity")
        try:
            quantity = int(quantity) if quantity is not None else None
        except (TypeError, ValueError):
            quantity = None

        age = eq.get("estimated_age")
        try:
            age = float(age) if age is not None else None
        except (TypeError, ValueError):
            age = None

        state = str(eq.get("state") or "Reportado").strip().title()
        if state not in {"Confirmado", "Reportado", "Estimado", "Desconocido"}:
            state = "Reportado"

        normalized_equipment.append(
            {
                "modality": normalize_modality(modality),
                "manufacturer": clean(eq.get("manufacturer")),
                "model": clean(eq.get("model")),
                "serial_number": clean(eq.get("serial_number")),
                "quantity": quantity if quantity is not None and 1 <= quantity <= 10000 else None,
                "estimated_age": age,
                "state": state,
            }
        )

    return {
        "customer_name": str(customer_name).strip(),
        "city": clean(data.get("city")),
        "country": normalize_country(data.get("country")),
        "equipment": normalized_equipment,
    }


def _extract_json_object(content: str) -> Dict[str, Any]:
    """Read the first complete object, tolerating model prose/fences after it.

    raw_decode respects strings and nested braces. Never combine separate
    objects or skip a malformed first object to accept one of its children.
    """
    content = (content or "").strip()
    start = content.find("{")
    if start < 0 or content.startswith("["):
        raise ValueError("QVAC did not return a JSON object")
    result, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(result, dict):
        raise ValueError("QVAC did not return a JSON object")
    return result


async def extract_observation(
    text: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extrae la observación usando QVAC LOCAL.

    No existe ruta de inferencia remota. Si QVAC no está disponible y
    QVAC_REQUIRED está activo, se genera un error claro para no incumplir
    silenciosamente el requisito del reto.
    """
    global _last_inference_ms
    global _last_inference_note

    if not _qvac_ready or _qvac_transport is None or _qvac_model_id is None:
        if QVAC_REQUIRED:
            raise QvacNotReadyError(
                _qvac_start_error
                or "QVAC todavía no está listo."
            )

        # Modo de desarrollo opcional, nunca etiquetado como IA.
        _last_inference_note = "deterministic_dev_fallback"
        return sanitize_extraction(
            _local_extract(text, context=context),
            source_text=text,
            context=context,
        )

    context_text = ""

    if context:
        context_text = (
            "\n\nCONTEXTO ACUMULADO DE LA CONVERSACIÓN:\n"
            + json.dumps(context, ensure_ascii=False)
            + "\n\nEl mensaje actual puede ser una respuesta corta. "
            "Devuelve el estado COMPLETO combinado."
        )

    history = [
        {
            "role": "system",
            "content": EXTRACTION_PROMPT,
        },
        {
            "role": "user",
            "content": (
                "OBSERVACIÓN ACTUAL:\n"
                + text
                + context_text
            ),
        },
    ]

    started = time.perf_counter()

    try:
        run = completion(
            _qvac_transport,
            model_id=_qvac_model_id,
            history=history,
            generation_params={"temp": 0, "predict": 900, "seed": 42},
        )

        # Do not cancel the SDK's result Future: its event pump owns completion.
        final = await asyncio.shield(run.final)
        content = final.content_text

        _last_inference_ms = int(
            (time.perf_counter() - started) * 1000
        )
        _last_inference_note = "qvac_local"

        try:
            parsed = _extract_json_object(content)
            normalized = _normalize_result(parsed)
            return sanitize_extraction(
                normalized,
                source_text=text,
                context=context,
            )

        except Exception as json_exc:
            # QVAC sí ejecutó la inferencia. Si el modelo pequeño produce
            # JSON imperfecto, el parser heurístico local evita romper la demo.
            # No se envía contenido fuera del dispositivo.
            print(
                "[QVAC] JSON imperfecto; reparación local determinística: "
                f"{json_exc}"
            )
            _last_inference_note = "qvac_local_plus_parser_repair"
            return sanitize_extraction(
                _local_extract(text, context=context),
                source_text=text,
                context=context,
            )

    except QvacNotReadyError:
        raise

    except Exception as exc:
        _last_inference_ms = int(
            (time.perf_counter() - started) * 1000
        )
        _last_inference_note = "qvac_runtime_error"
        await _shutdown_unlocked()

        raise QvacNotReadyError(
            f"La inferencia QVAC local falló: {exc}"
        ) from exc


# Marcas conocidas + algunas sintéticas útiles para la demo. El extractor
# también puede capturar marcas desconocidas por contexto ("MR de X").
MANUFACTURERS = [
    "Siemens", "GE", "GE Healthcare", "Philips", "Canon",
    "Toshiba", "Mindray", "Samsung", "MedTech Nova",
    "NovaMed", "HealthTech", "LumenWorks", "Demo Imaging",
]


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _local_extract(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extractor heurístico local usado solo como reparación/desarrollo.

    Está pensado para observaciones del reto: cliente, ubicación, modalidad,
    cantidad, fabricante, modelo y edad. No sustituye la inferencia QVAC;
    solo evita que un JSON imperfecto rompa el flujo local.
    """
    result = _normalize_result(context or _empty_result())
    lower = _fold(text)

    def eq_for(modality: Optional[str], missing_field: Optional[str] = None):
        candidates = result.get("equipment", [])
        if modality:
            exact = next((eq for eq in candidates if _fold(eq.get("modality", "")) == _fold(modality)), None)
            if exact:
                return exact
        if missing_field:
            return next((eq for eq in candidates if not eq.get(missing_field)), candidates[0] if candidates else None)
        return candidates[0] if candidates else None

    def modalities_in_message():
        return equipment_mentions(text)

    # Cliente/hospital: conserva contexto si ya existe.
    if is_missing(result.get("customer_name")):
        m = re.search(
            r"\b(?:hospital|clinica|clínica|centro)\s+([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 .&'-]{1,50}?)(?=\s+(?:en|in|tiene|tienen|has|have|con|with)\b|[,.]|$)",
            text, re.I,
        )
        if m:
            prefix_match = re.search(r"\b(hospital|clinica|clínica|centro)\b", m.group(0), re.I)
            prefix = prefix_match.group(1) if prefix_match else "Hospital"
            result["customer_name"] = f"{prefix.title()} {m.group(1).strip()}"

    # Países explícitos comunes del reto. Esto es parsing determinístico local,
    # no geolocalización ni inferencia de red.
    if not result.get("country"):
        for alias, canonical in country_aliases().items():
            if re.search(rf"\b{re.escape(alias)}\b", lower):
                result["country"] = canonical
                break

    if not result.get("city") and re.search(r"\bciudad de panama\b", lower):
        result["city"] = "Ciudad de Panamá"
        result["country"] = result.get("country") or "Panamá"

    # Ubicación: "en Ciudad Demo" / "en São Paulo, Brasil".
    if not result.get("city"):
        loc = re.search(
            r"\b(?:en|in)\s+([A-Za-zÀ-ÿ .'-]{2,40}?)(?:,\s*([A-Za-zÀ-ÿ .'-]{2,30}))?(?=[.;]|\s+(?:vi|saw|tiene|tienen|has|have|con|with)\b|$)",
            text, re.I,
        )
        if loc:
            city_candidate = loc.group(1).strip()
            if not re.match(r"^(hospital|clinica|clínica|centro)\b", city_candidate, re.I):
                result["city"] = None if normalize_country(city_candidate) == result.get("country") else city_candidate.strip(" ,.;:!?")
                if loc.group(2) and not result.get("country"):
                    result["country"] = loc.group(2).strip(" ,.;:!?")

    found = modalities_in_message()

    # Crear/actualizar modalidades y cantidades.
    used_rows = set()
    for hit in found:
        modality, quantity = hit["modality"], hit["quantity"]
        existing = next((eq for eq in result["equipment"] if id(eq) not in used_rows and _fold(eq.get("modality", "")) == _fold(modality)), None)
        if not existing:
            existing = {
                "modality": modality,
                "manufacturer": None,
                "model": None,
                "serial_number": None,
                "quantity": quantity,
                "estimated_age": None,
                "state": "Reportado",
            }
            result["equipment"].append(existing)
        else:
            existing["quantity"] = quantity or existing.get("quantity")
        used_rows.add(id(existing))

        # 1) Fabricante conocido DESPUÉS de la modalidad y antes de la
        # siguiente modalidad. Así "MR Siemens, dos CT GE" no asigna Siemens
        # también al CT.
        later_starts = [h["start"] for h in found if h["start"] > hit["start"]]
        segment_end = min(later_starts) if later_starts else min(len(text), hit["end"] + 100)
        after_segment = text[hit["end"]:segment_end]
        manufacturer = next(
            (m for m in sorted(MANUFACTURERS, key=len, reverse=True) if re.search(rf"\b{re.escape(_fold(m))}\b", _fold(after_segment))),
            None,
        )

        # 2) Fabricante genérico: "MR de MedTech Nova y un CT".
        if not manufacturer:
            tail = text[hit["end"]: segment_end]
            generic = re.match(
                r"\s+(?:de|from|marca|fabricante)\s+([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 .&+_-]{1,45}?)(?=\s+(?:y|and)\s+(?:un|una|uno|one|dos|two|tres|three|\d+)\b|[,.;!?]|$)",
                tail,
                re.I,
            )
            if generic:
                candidate = generic.group(1).strip(" -")
                if not re.search(r"\b(?:anos|años|years?|modelo|model)\b", _fold(candidate)):
                    manufacturer = candidate

        if manufacturer and not existing.get("manufacturer"):
            existing["manufacturer"] = manufacturer
        # A model must be literal text after a manufacturer, never brand knowledge.
        if manufacturer:
            model_tail = re.search(re.escape(manufacturer) + r"\s+([A-Z][A-Za-z0-9+_-]*(?:\s+[A-Z0-9][A-Za-z0-9+_-]*)*)", after_segment)
            if model_tail:
                existing["model"] = model_tail[1]
        existing["estimated_age"] = explicit_age(hit["text"])


    # Modalidad mencionada explícitamente en el mensaje actual, útil para
    # respuestas de seguimiento del tipo "el modelo del MR es...".
    current_modality = found[0]["modality"] if len({h["modality"] for h in found}) == 1 and found else None

    # Fabricante explícito en respuestas de seguimiento.
    explicit_manu = re.search(
        r"\b(?:fabricante|marca)\s*(?:es|is|:)?\s*([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 .&+_-]{1,45}?)(?=[,.;!?]|$)",
        text,
        re.I,
    )
    mentioned_manufacturer = next((m for m in sorted(MANUFACTURERS, key=len, reverse=True) if _fold(m) in lower), None)
    if explicit_manu:
        mentioned_manufacturer = explicit_manu.group(1).strip()

    # La marca global solo se aplica como respuesta de seguimiento o cuando
    # el usuario la declara explícitamente ("fabricante es X"). En una
    # observación con varios equipos, la asociación ya se hizo por cercanía.
    if mentioned_manufacturer and result["equipment"] and (context is not None or explicit_manu):
        target = eq_for(current_modality, "manufacturer")
        if target and not target.get("manufacturer"):
            target["manufacturer"] = mentioned_manufacturer

    # Modelo: "el modelo es NovaScan X1", "el modelo del MR es NovaScan X1".
    model_match = re.search(
        r"\b(?:modelo|model)(?:\s+(?:del|de|of)\s+[A-Za-z0-9-]+)?\s*(?:es|is|:)\s*([A-Za-z0-9][A-Za-z0-9 ._+/-]{1,45}?)(?=[,.;!?]|$)",
        text,
        re.I,
    )
    if not model_match:
        model_match = re.search(
            r"\b(?:modelo|model)\s+([A-Za-z0-9][A-Za-z0-9._+/-]{1,30})(?=[,.;!?]|$)",
            text,
            re.I,
        )
    if model_match and result["equipment"]:
        target = eq_for(current_modality, "model")
        if target:
            target["model"] = model_match.group(1).strip()

    # Edades asociadas a la modalidad mencionada más cerca antes de la edad.
    for age_match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:anos|años|years?)", lower, re.I):
        age_pos = age_match.start()
        nearest = None
        nearest_distance = 10**9
        for hit in found:
            if hit["end"] <= age_pos:
                distance = age_pos - hit["end"]
                if distance < nearest_distance and distance <= 130:
                    nearest = hit["modality"]
                    nearest_distance = distance
        if nearest:
            target_eq = eq_for(nearest)
            if target_eq:
                target_eq["estimated_age"] = float(age_match.group(1).replace(",", "."))

    if context and not found:
        parts = str(context.get("_question_key") or "").split(".")
        if len(parts) == 3 and parts[0] == "equipment":
            index, field = int(parts[1]), parts[2]
            if index < len(result["equipment"]):
                value = clean(text)
                if field == "estimated_age":
                    result["equipment"][index][field] = explicit_age(text)
                elif field in {"model", "manufacturer", "serial_number"} and value and len(value.split()) <= 6:
                    result["equipment"][index][field] = value
        elif context.get("_question_key") == "customer_name" and len(text.split()) <= 12:
            result["customer_name"] = clean(text)

    return sanitize_extraction(
        _normalize_result(result),
        source_text=text,
        context=context,
    )

def is_missing(value) -> bool:
    return value is None or value == "" or value in ("Desconocido", "Unknown")


def merge_extracted_data(previous: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    # The extractor returns complete grounded context, including omitted prior rows.
    return _normalize_result(new)


_runtime_lock = asyncio.Lock()


def _serialized(operation, timeout):
    async def guarded(*args, **kwargs):
        # A bounded queue prevents unlimited concurrent requests from consuming RAM.
        try:
            await asyncio.wait_for(_runtime_lock.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise QvacNotReadyError("QVAC está ocupado. Vuelve a intentar en unos segundos.") from exc
        try:
            return await asyncio.wait_for(operation(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await _shutdown_unlocked()
            raise QvacNotReadyError("QVAC excedió el tiempo límite. Reinicia el backend para recuperar el worker.") from exc
        finally:
            _runtime_lock.release()
    return guarded


_shutdown_unlocked = shutdown_qvac
initialize_qvac = _serialized(initialize_qvac, QVAC_STARTUP_TIMEOUT_SECONDS)
extract_observation = _serialized(extract_observation, QVAC_INFERENCE_TIMEOUT_SECONDS)
transcribe_audio_file = _serialized(transcribe_audio_file, QVAC_INFERENCE_TIMEOUT_SECONDS)
shutdown_qvac = _serialized(shutdown_qvac, 20)
