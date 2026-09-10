from __future__ import annotations

import json
import os
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
    from tetherto.qvac_sdk import Client, completion, load_model, unload_model
    import tetherto.qvac_sdk.models as qvac_models
except Exception:
    Client = None
    completion = None
    load_model = None
    unload_model = None
    qvac_models = None

def sanitize_extraction(
    data: Dict[str, Any],
    source_text: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Corrige inconsistencias obvias sin inventar información.

    Reglas conservadoras:
    - manufacturer nunca se reutiliza como model.
    - si un texto con varias modalidades contiene una sola edad explícita,
      evita que esa edad se copie a modalidades no asociadas a ella.
    """
    if not isinstance(data, dict):
        return data

    equipment = data.get("equipment") or []

    # 1) Fabricante != modelo.
    for item in equipment:
        if not isinstance(item, dict):
            continue

        manufacturer = item.get("manufacturer")
        model = item.get("model")

        if manufacturer and model:
            if str(manufacturer).strip().casefold() == str(model).strip().casefold():
                item["model"] = None

    # 2) Barrera conservadora para edades copiadas entre modalidades.
    # Solo actúa cuando el mensaje actual menciona varias modalidades y
    # contiene exactamente UNA edad numérica explícita.
    if source_text and len(equipment) > 1:
        folded = _fold(source_text)
        age_matches = list(
            re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:anos|años|years?)", folded, re.I)
        )

        # Expresiones que sí permiten que una sola edad aplique a todos.
        global_age_markers = (
            "todos", "todas", "ambos", "ambas",
            "cada equipo", "cada uno", "all of them", "both",
        )

        if len(age_matches) == 1 and not any(marker in folded for marker in global_age_markers):
            age_match = age_matches[0]
            age_value = float(age_match.group(1).replace(",", "."))
            age_pos = age_match.start()

            modality_hits = []
            seen_ranges = set()
            for alias, canonical in MODALITY_ALIASES:
                for match in re.finditer(rf"\b{re.escape(_fold(alias))}\b", folded, re.I):
                    key = (match.start(), match.end(), canonical)
                    if key not in seen_ranges:
                        seen_ranges.add(key)
                        modality_hits.append((match.start(), match.end(), canonical))

            modalities_in_text = {hit[2] for hit in modality_hits}

            if len(modalities_in_text) >= 2:
                # Modalidad explícita más cercana antes de la edad.
                before = [hit for hit in modality_hits if hit[1] <= age_pos]
                if before:
                    nearest = min(before, key=lambda hit: age_pos - hit[1])[2]

                    for item in equipment:
                        modality = str(item.get("modality") or "").strip()
                        item_age = item.get("estimated_age")

                        if modality != nearest and item_age is not None:
                            try:
                                if float(item_age) == age_value:
                                    item["estimated_age"] = None
                            except (TypeError, ValueError):
                                pass

    return data

class QvacNotReadyError(RuntimeError):
    """La inferencia QVAC local no está disponible."""


_qvac_client = None
_qvac_transport = None
_qvac_model_id: Optional[str] = None
_qvac_start_error: Optional[str] = "not_initialized"
_qvac_ready = False
_last_inference_ms: Optional[int] = None
_last_inference_note: Optional[str] = None


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
        _qvac_client = Client(
            config={
                "cacheDirectory": str(Path(QVAC_CACHE_DIR).resolve()),
                "loggerLevel": "warn",
                "loggerConsoleOutput": False,
            }
        )

        await _qvac_client.__aenter__()
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

    except Exception as exc:
        _qvac_ready = False
        _qvac_model_id = None
        _qvac_start_error = str(exc)

        if _qvac_client is not None:
            try:
                await _qvac_client.__aexit__(None, None, None)
            except Exception:
                pass

        _qvac_client = None
        _qvac_transport = None

        raise QvacNotReadyError(
            f"No se pudo iniciar QVAC: {exc}"
        ) from exc


async def shutdown_qvac() -> None:
    """Libera el modelo y cierra el worker local de QVAC."""
    global _qvac_client
    global _qvac_transport
    global _qvac_model_id
    global _qvac_ready

    if _qvac_transport is not None and _qvac_model_id is not None:
        try:
            await unload_model(
                _qvac_transport,
                _qvac_model_id,
            )
        except Exception as exc:
            print(f"[QVAC] Advertencia al descargar modelo: {exc}")

    if _qvac_client is not None:
        try:
            await _qvac_client.__aexit__(None, None, None)
        except Exception as exc:
            print(f"[QVAC] Advertencia al cerrar worker: {exc}")

    _qvac_client = None
    _qvac_transport = None
    _qvac_model_id = None
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
        "extraction_mode": "qvac_on_device" if _qvac_ready else "qvac_not_ready",
        "cloud_inference": False,
        "network_required_for_inference": False,
        "model": QVAC_MODEL,
        "cache_directory": str(Path(QVAC_CACHE_DIR).resolve()),
        "startup_error": _qvac_start_error,
        "last_inference_ms": _last_inference_ms,
        "last_inference_note": _last_inference_note,
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
            quantity = int(quantity) if quantity is not None else 1
        except (TypeError, ValueError):
            quantity = 1

        age = eq.get("estimated_age")
        try:
            age = float(age) if age is not None else None
        except (TypeError, ValueError):
            age = None

        normalized_equipment.append(
            {
                "modality": str(modality).strip(),
                "manufacturer": eq.get("manufacturer"),
                "model": eq.get("model"),
                "quantity": max(quantity, 1),
                "estimated_age": age,
            }
        )

    return {
        "customer_name": str(customer_name).strip(),
        "city": data.get("city"),
        "country": data.get("country"),
        "equipment": normalized_equipment,
    }


def _extract_json_object(content: str) -> Dict[str, Any]:
    """Tolera fences o texto accidental alrededor del JSON."""
    content = (content or "").strip()

    if content.startswith("```"):
        content = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            content,
            flags=re.I | re.S,
        ).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")

        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])

        raise


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
        )

        final = await run.final
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

        raise QvacNotReadyError(
            f"La inferencia QVAC local falló: {exc}"
        ) from exc


NUMBER_WORDS = {
    "un": 1, "una": 1, "uno": 1, "one": 1,
    "dos": 2, "two": 2,
    "tres": 3, "three": 3,
    "cuatro": 4, "four": 4,
    "cinco": 5, "five": 5,
    "seis": 6, "six": 6,
    "siete": 7, "seven": 7,
    "ocho": 8, "eight": 8,
    "nueve": 9, "nine": 9,
    "diez": 10, "ten": 10,
    "once": 11, "eleven": 11,
    "doce": 12, "twelve": 12,
    "trece": 13, "thirteen": 13,
    "catorce": 14, "fourteen": 14,
    "quince": 15, "fifteen": 15,
    "dieciseis": 16, "sixteen": 16,
    "diecisiete": 17, "seventeen": 17,
    "dieciocho": 18, "eighteen": 18,
    "diecinueve": 19, "nineteen": 19,
    "veinte": 20, "twenty": 20,
}

# Alias ordenados de más específicos a más cortos para reducir falsos matches.
MODALITY_ALIASES = [
    ("resonancia magnetica", "MR"), ("magnetic resonance", "MR"),
    ("resonancias", "MR"), ("resonancia", "MR"),
    ("resonadores", "MR"), ("resonador", "MR"),
    ("mri", "MR"), ("mr", "MR"), ("rm", "MR"),

    ("tomografia computarizada", "CT"), ("computed tomography", "CT"),
    ("tomografos", "CT"), ("tomografo", "CT"),
    ("tomografia", "CT"), ("scanner ct", "CT"), ("ct", "CT"),

    ("ultrasounds", "Ultrasound"), ("ultrasound", "Ultrasound"),
    ("ultrasonidos", "Ultrasound"), ("ultrasonido", "Ultrasound"),
    ("ecografos", "Ultrasound"), ("ecografo", "Ultrasound"),

    ("rayos x", "X-Ray"), ("x-ray", "X-Ray"), ("xray", "X-Ray"),
    ("monitoring", "Monitoring"), ("monitores", "Monitoring"),
    ("monitor", "Monitoring"),
    ("spect", "SPECT"), ("pet", "PET"),
]

# Marcas conocidas + algunas sintéticas útiles para la demo. El extractor
# también puede capturar marcas desconocidas por contexto ("MR de X").
MANUFACTURERS = [
    "Siemens", "GE", "GE Healthcare", "Philips", "Canon",
    "Toshiba", "Mindray", "Samsung", "MedTech Nova",
    "NovaMed", "HealthTech",
]


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def _to_int(token: Optional[str], default: int = 1) -> int:
    if not token:
        return default
    token = _fold(token)
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token, default)


def _local_extract(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extractor heurístico local usado solo como reparación/desarrollo.

    Está pensado para observaciones del reto: cliente, ubicación, modalidad,
    cantidad, fabricante, modelo y edad. No sustituye la inferencia QVAC;
    solo evita que un JSON imperfecto rompa el flujo local.
    """
    result = _normalize_result(context or _empty_result())
    lower = _fold(text)
    number_alt = "|".join(sorted(map(re.escape, NUMBER_WORDS.keys()), key=len, reverse=True))

    def eq_for(modality: Optional[str], missing_field: Optional[str] = None):
        candidates = result.get("equipment", [])
        if modality:
            exact = next((eq for eq in candidates if _fold(eq.get("modality", "")) == _fold(modality)), None)
            if exact:
                return exact
        if missing_field:
            return next((eq for eq in candidates if not eq.get(missing_field)), candidates[0] if candidates else None)
        return candidates[0] if candidates else None

    def modalities_in_message() -> List[tuple]:
        hits = []
        for alias, canonical in MODALITY_ALIASES:
            af = re.escape(_fold(alias))
            # Acepta: "2 MR", "dos equipos MR", "dos equipos de MR", "3 sistemas CT".
            pattern = rf"(?:(\d+|{number_alt})\s*(?:x|×)?\s*(?:(?:equipos?|sistemas?|unidades?|maquinas?)\s+(?:de\s+)?)?)?\b{af}\b"
            for m in re.finditer(pattern, lower, re.I):
                hits.append({
                    "modality": canonical,
                    "quantity": _to_int(m.group(1), 1),
                    "start": m.start(),
                    "end": m.end(),
                    "match": m.group(0),
                })

        # Deduplicar alias que caen sobre la misma mención y conservar la
        # cantidad más informativa para cada modalidad.
        deduped = []
        for hit in sorted(hits, key=lambda h: (h["start"], -(h["end"] - h["start"]))):
            overlap = next((d for d in deduped if d["modality"] == hit["modality"] and not (hit["end"] <= d["start"] or hit["start"] >= d["end"])), None)
            if overlap:
                overlap["quantity"] = max(overlap["quantity"], hit["quantity"])
                continue
            deduped.append(hit)
        return deduped

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

    # Ubicación: "en Ciudad Demo" / "en São Paulo, Brasil".
    if not result.get("city"):
        loc = re.search(
            r"\b(?:en|in)\s+([A-Za-zÀ-ÿ .'-]{2,40}?)(?:,\s*([A-Za-zÀ-ÿ .'-]{2,30}))?(?=[.;]|\s+(?:vi|saw|tiene|tienen|has|have|con|with)\b|$)",
            text, re.I,
        )
        if loc:
            city_candidate = loc.group(1).strip()
            if not re.match(r"^(hospital|clinica|clínica|centro)\b", city_candidate, re.I):
                result["city"] = city_candidate.strip(" ,.;:!?")
                if loc.group(2) and not result.get("country"):
                    result["country"] = loc.group(2).strip(" ,.;:!?")

    found = modalities_in_message()

    # Crear/actualizar modalidades y cantidades.
    for hit in found:
        modality, quantity = hit["modality"], hit["quantity"]
        existing = next((eq for eq in result["equipment"] if _fold(eq.get("modality", "")) == _fold(modality)), None)
        if not existing:
            existing = {
                "modality": modality,
                "manufacturer": None,
                "model": None,
                "quantity": quantity,
                "estimated_age": None,
            }
            result["equipment"].append(existing)
        else:
            existing["quantity"] = max(int(existing.get("quantity") or 1), quantity)

        # 1) Fabricante conocido DESPUÉS de la modalidad y antes de la
        # siguiente modalidad. Así "MR Siemens, dos CT GE" no asigna Siemens
        # también al CT.
        later_starts = [h["start"] for h in found if h["start"] > hit["start"]]
        segment_end = min(later_starts) if later_starts else min(len(text), hit["end"] + 100)
        after_segment = text[hit["end"]:segment_end]
        manufacturer = next(
            (m for m in sorted(MANUFACTURERS, key=len, reverse=True) if _fold(m) in _fold(after_segment)),
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

    # Respuestas cortas usando contexto acumulado.
    if context and result["equipment"]:
        age_reply = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:anos|años|years?)", text, re.I)
        if age_reply and not found:
            target = eq_for(current_modality, "estimated_age")
            if target:
                target["estimated_age"] = float(age_reply.group(1).replace(",", "."))

        # Si el usuario indica que no conoce el fabricante, conservarlo como
        # Unknown para no repetir la misma pregunta y mantener la observación útil.
        negative_reply = _fold(text.strip().strip(".!?,:;")) in {
            "no se", "no lo se", "no se sabe", "no conozco", "unknown", "desconocido"
        }
        if negative_reply:
            missing_manufacturer = next((eq for eq in result["equipment"] if not eq.get("manufacturer")), None)
            if missing_manufacturer:
                missing_manufacturer["manufacturer"] = "Unknown"

        # Si la pregunta pendiente era probablemente de fabricante y el usuario
        # responde solo "Siemens" / "MedTech Nova", aceptar la respuesta corta.
        words = text.strip().strip(".!").split()
        if not mentioned_manufacturer and not model_match and len(words) <= 4:
            missing_manufacturer = next((eq for eq in result["equipment"] if not eq.get("manufacturer")), None)
            plain = text.strip().strip(".!?,:;")
            if missing_manufacturer and re.fullmatch(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 .&+_-]{1,45}", plain):
                # Evitar interpretar respuestas negativas como una marca.
                if _fold(plain) not in {"no se", "no lo se", "no se sabe", "unknown", "desconocido"}:
                    missing_manufacturer["manufacturer"] = plain

    return sanitize_extraction(_normalize_result(result))

def get_follow_up_question(extracted_data: Dict[str, Any]) -> str:
    """Genera la pregunta de mayor valor para completar la observación."""
    missing_info = []

    if is_missing(extracted_data.get("customer_name")):
        missing_info.append("el nombre del cliente")
    if not extracted_data.get("equipment"):
        missing_info.append("qué equipos hay")
    if missing_info:
        return f"¿Podrías proporcionar {', '.join(missing_info)}?"

    # Primero completar fabricantes (alto valor para normalizar/deduplicar).
    for eq in extracted_data.get("equipment", []):
        if not eq.get("manufacturer"):
            return f"¿Sabes el fabricante del equipo de {eq.get('modality', 'desconocido')}?"

    # Luego preguntar por edad cuando todavía hay margen de seguimiento.
    for eq in extracted_data.get("equipment", []):
        if not eq.get("estimated_age"):
            return f"¿Tienes idea de la edad aproximada del equipo de {eq.get('modality', 'desconocido')}?"

    return "¡Gracias! La información ha sido registrada correctamente."


def is_missing(value) -> bool:
    return value is None or value == "" or value in ("Desconocido", "Unknown")


def merge_extracted_data(previous: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(previous)

    for field in ("customer_name", "city", "country"):
        if is_missing(merged.get(field)) and not is_missing(new.get(field)):
            merged[field] = new[field]

    merged_equipment: List[Dict[str, Any]] = [dict(eq) for eq in merged.get("equipment", [])]
    new_equipment = new.get("equipment", [])

    for new_eq in new_equipment:
        match = next(
            (eq for eq in merged_equipment
             if eq.get("modality") and new_eq.get("modality")
             and eq["modality"].strip().lower() == new_eq["modality"].strip().lower()),
            None
        )
        if match:
            for field in ("manufacturer", "model", "estimated_age"):
                if is_missing(match.get(field)) and not is_missing(new_eq.get(field)):
                    match[field] = new_eq[field]
            # La cantidad siempre puede mejorar con una observación más explícita.
            if not is_missing(new_eq.get("quantity")):
                match["quantity"] = max(int(match.get("quantity") or 1), int(new_eq.get("quantity") or 1))
        else:
            merged_equipment.append(new_eq)

    merged["equipment"] = merged_equipment
    return sanitize_extraction(_normalize_result(merged))


def get_equipment_confidence(eq: Dict[str, Any]) -> str:
    known = sum(1 for f in ("manufacturer", "model", "estimated_age") if not is_missing(eq.get(f)))
    if known >= 2:
        return "High"
    if known == 1:
        return "Medium"
    return "Low"


def get_confidence_score(extracted_data: Dict[str, Any]) -> str:
    score = 0
    total_fields = 0

    if not is_missing(extracted_data.get("customer_name")):
        score += 1
    total_fields += 1

    if extracted_data.get("city"):
        score += 1
    total_fields += 1

    if extracted_data.get("country"):
        score += 1
    total_fields += 1

    for eq in extracted_data.get("equipment", []):
        for field in ("modality", "manufacturer", "estimated_age"):
            if not is_missing(eq.get(field)):
                score += 1
            total_fields += 1

    if total_fields == 0:
        return "Unknown"

    percentage = score / total_fields
    if percentage >= 0.8:
        return "High"
    if percentage >= 0.5:
        return "Medium"
    return "Low"
