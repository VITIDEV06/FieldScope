from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text as sql_text, or_
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from contextlib import asynccontextmanager
import sys
import os
from pathlib import Path
# Agregar la carpeta actual al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import json
import uuid
import datetime

from database import get_db, engine
from models import utcnow, Base, Customer, Equipment, Observation, PendingSession, CountryRegion, ObservationEquipment
from schemas import CustomerResponse, EquipmentResponse, ObservationResponse
from ai import (
    extract_observation,
    transcribe_audio_file,
    merge_extracted_data,
    is_missing,
    get_ai_runtime_status,
    initialize_qvac,
    shutdown_qvac,
    QvacNotReadyError,
)

import asyncio
import io
import wave
import tempfile
from starlette.responses import JSONResponse
from normalization import (normalize_country, normalize_modality, fold, equipment_mentions,
                           country_aliases, country_catalog, NUMBER_PATTERN, number)
from config import (MAX_FOLLOW_UP_ROUNDS, MAX_CAPTURE_ROUNDS, MAX_TEXT_LENGTH,
                    MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, FRESH_DAYS, STALE_DAYS,
                    REFRESH_OPPORTUNITY_YEARS, CORS_ORIGINS)


# Crear tablas que todavía no existan.
Base.metadata.create_all(bind=engine)


def run_database_migrations() -> None:
    """
    Conserva bases SQLite existentes y agrega columnas incorporadas por
    versiones posteriores del prototipo. Las migraciones son idempotentes.
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    equipment_migrations = {
        "observation_state": "VARCHAR DEFAULT 'Reportado'",
        "times_reported": "INTEGER DEFAULT 1",
        "last_updated": "DATETIME",
        "last_verified": "DATETIME",
        "confidence_score": "FLOAT DEFAULT 0.5",
        "source": "VARCHAR DEFAULT 'Conversación'",
        "photo_path": "VARCHAR",
        "serial_number": "VARCHAR",
    }
    observation_migrations = {
        "observation_state": "VARCHAR DEFAULT 'Reportado'",
        "is_duplicate_update": "VARCHAR DEFAULT 'No'",
        "last_verified": "DATETIME",
        "confidence_score": "FLOAT DEFAULT 0.5",
        "source": "VARCHAR DEFAULT 'Conversación'",
        "photo_path": "VARCHAR",
        "structured_json": "TEXT",
    }
    session_migrations = {
        "source": "VARCHAR DEFAULT 'Conversación'",
        "photo_path": "VARCHAR",
    }

    with engine.begin() as connection:
        if "equipment" in table_names:
            current = {column["name"] for column in inspector.get_columns("equipment")}
            for column_name, sql_type in equipment_migrations.items():
                if column_name not in current:
                    connection.execute(
                        sql_text(
                            f"ALTER TABLE equipment ADD COLUMN {column_name} {sql_type}"
                        )
                    )
                    print(f"[DB] Migración aplicada: equipment.{column_name}")

            # Completar filas históricas sin destruir datos.
            connection.execute(
                sql_text(
                    "UPDATE equipment "
                    "SET times_reported = COALESCE(times_reported, 1), "
                    "observation_state = COALESCE(observation_state, 'Reportado'), "
                    "last_verified = COALESCE(last_verified, last_updated), "
                    "confidence_score = COALESCE(confidence_score, 0.5), "
                    "source = COALESCE(source, 'Conversación')"
                )
            )

        if "observations" in table_names:
            current = {column["name"] for column in inspector.get_columns("observations")}
            for column_name, sql_type in observation_migrations.items():
                if column_name not in current:
                    connection.execute(
                        sql_text(
                            f"ALTER TABLE observations ADD COLUMN {column_name} {sql_type}"
                        )
                    )
                    print(f"[DB] Migración aplicada: observations.{column_name}")

            connection.execute(
                sql_text(
                    "UPDATE observations "
                    "SET observation_state = COALESCE(observation_state, 'Reportado'), "
                    "is_duplicate_update = COALESCE(is_duplicate_update, 'No'), "
                    "last_verified = COALESCE(last_verified, submitted_at), "
                    "confidence_score = COALESCE(confidence_score, 0.5), "
                    "source = COALESCE(source, 'Conversación')"
                )
            )

        if "pending_sessions" in table_names:
            current = {column["name"] for column in inspector.get_columns("pending_sessions")}
            for column_name, sql_type in session_migrations.items():
                if column_name not in current:
                    connection.execute(
                        sql_text(f"ALTER TABLE pending_sessions ADD COLUMN {column_name} {sql_type}")
                    )


# Aplicar la migración también al importar el backend. Esto hace que una base
# antigua quede lista incluso antes de la primera consulta de la API.
run_database_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    QVAC se carga en el dispositivo al iniciar la aplicación y se reutiliza
    para todas las observaciones. La inferencia se mantiene en el dispositivo.
    """
    try:
        run_database_migrations()
        await initialize_qvac()
    except QvacNotReadyError as exc:
        # El backend puede iniciar para permitir diagnóstico vía /api/health,
        # pero /api/observations/process devolverá 503 hasta que QVAC esté listo.
        print(f"[QVAC] No disponible al iniciar: {exc}")

    try:
        yield
    finally:
        await shutdown_qvac()


app = FastAPI(
    title="Fieldscope Customer Installed Base Intelligence",
    description=(
        "Captura conversacional con inferencia QVAC en el dispositivo, "
        "almacenamiento local y Customer 360."
    ),
    version="2.0.0-qvac",
    lifespan=lifespan,
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)

# Single local worker / SQLite writer: serialize mutations, including confirmation
# retries, so concurrent requests cannot save the same pending session twice.
_write_lock = asyncio.Lock()


@app.middleware("http")
async def local_request_guard(request, call_next):
    host = request.headers.get("host", "").split(":")[0].lower()
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "Local host required"}, status_code=400)
    origin = request.headers.get("origin")
    if origin and origin not in CORS_ORIGINS and origin not in {"http://127.0.0.1:8001", "http://localhost:8001"}:
        return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    if request.method not in {"POST", "PATCH", "DELETE"}:
        return await call_next(request)
    try:
        await asyncio.wait_for(_write_lock.acquire(), timeout=5)
    except asyncio.TimeoutError:
        return JSONResponse({"detail": "Otra captura está en curso. Vuelve a intentar."}, status_code=409)
    try:
        if request.url.path != "/api/voice/transcribe":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32_768:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    finally:
        _write_lock.release()


# ==================== ESQUEMAS ====================

class ObservationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    submitted_by: str = Field(default="Field Employee", min_length=1, max_length=100)
    session_id: Optional[str] = Field(default=None, max_length=36)  # si viene, es la respuesta a una pregunta de seguimiento


class CountryRegionRequest(BaseModel):
    country_name: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=60)


class CustomerCountryUpdate(BaseModel):
    country: str = Field(min_length=1, max_length=100)


class ConfirmObservationRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)


class NaturalQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


def infer_observation_state(
    raw_texts: List[str],
    confirmed_by_repeat: bool = False,
) -> str:
    """
    Estado exigido por el reto:
    Confirmado / Reportado / Estimado / Desconocido.

    - Confirmado: una observación coincide con equipamiento ya reportado.
    - Estimado: contiene lenguaje explícitamente aproximado.
    - Reportado: observación directa nueva sin marcador de estimación.
    - Desconocido: reservado para información sin evidencia suficiente.
    """
    if confirmed_by_repeat:
        return "Confirmado"

    text = " ".join(raw_texts).lower()

    estimated_markers = (
        "aproximadamente",
        "aprox",
        "parece",
        "parecen",
        "unos ",
        "unas ",
        "alrededor",
        "cerca de",
        "más o menos",
        "mas o menos",
        "estimated",
        "approximately",
        "around",
        "about ",
        "looks ",
        "appears ",
    )

    if any(marker in text for marker in estimated_markers):
        return "Estimado"

    return "Reportado"


def is_explicit_unknown_reply(text: str) -> bool:
    """Reconoce respuestas que indican explícitamente que el dato no se conoce."""
    normalized = (text or "").strip().lower().strip(" .,!?:;")
    exact = {
        "no sé", "no se", "no lo sé", "no lo se",
        "no se sabe", "no conozco", "desconocido", "unknown",
        "no recuerdo",
    }
    if normalized in exact:
        return True
    return normalized.startswith((
        "no recuerdo ", "no me acuerdo ", "no conozco ",
        "no sé ", "no se ", "i don't know", "i dont know",
    ))


# ==================== ENDPOINTS BÁSICOS ====================

@app.get("/")
def inicio():
    return {
        "mensaje": "Customer Installed Base Intelligence funcionando",
        "estado": "OK"
    }

@app.get("/api/health")
def health():
    """Estado del backend y modo real del extractor."""
    return {
        "status": "ok",
        **get_ai_runtime_status(),
    }


@app.post("/api/voice/transcribe")
async def transcribe_voice(request: Request):
    """
    Recibe un WAV 16 kHz mono desde el navegador y lo transcribe con
    QVAC WHISPER_TINY en este dispositivo. El audio no se envía a la nube.
    """
    mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(415, "Se requiere audio/wav PCM mono 16 kHz.")
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "La grabación supera el límite de 2 MiB.")
    if not audio:
        raise HTTPException(400, "No se recibió audio.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError("Expected PCM16 mono 16 kHz")
            frames = wav.getnframes()
            if not 5600 <= frames <= MAX_AUDIO_SECONDS * 16000:
                raise ValueError("Audio must be 0.35–60 seconds")
            if len(wav.readframes(frames)) != frames * 2:
                raise ValueError("Truncated WAV")
    except (wave.Error, EOFError, ValueError) as exc:
        raise HTTPException(400, "Audio inválido: WAV PCM16 mono 16 kHz, entre 0.35 y 60 segundos.") from exc
    audio_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", prefix="fieldscope-voice-", delete=False) as handle:
            handle.write(audio)
            audio_path = Path(handle.name)
        transcript = await transcribe_audio_file(str(audio_path))
        return {"status": "success", "text": transcript, "provider": "QVAC",
                "model": "WHISPER_TINY", "inference_location": "on_device", "cloud_inference": False}
    except QvacNotReadyError as exc:
        raise HTTPException(503, {"code": "qvac_asr_not_ready",
                                  "message": "ASR local no disponible. Comprueba qvac_voice_warmup.py."}) from exc
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)

# ==================== ENDPOINTS DE OBSERVACIONES ====================

def _session_payload(session: Optional[PendingSession]) -> tuple[Optional[dict], list[str], Optional[str]]:
    if not session:
        return None, [], None
    raw = json.loads(session.accumulated_data)
    if isinstance(raw, dict) and "data" in raw:
        return raw.get("data") or {}, raw.get("unknown_fields") or [], raw.get("question_key")
    return raw, [], None


def _next_question(extracted: dict, unknown_fields: list[str]) -> tuple[Optional[str], Optional[str]]:
    if is_missing(extracted.get("customer_name")) and "customer_name" not in unknown_fields:
        return "customer_name", "¿Cuál es el nombre del hospital, clínica o cliente?"
    if not extracted.get("equipment"):
        return "equipment", "¿Qué equipo médico observaste?"

    for field, question in (
        ("manufacturer", "¿Conoces el fabricante de los equipos {label}?"),
        ("estimated_age", "¿Conoces aproximadamente la antigüedad de los equipos {label}?"),
        ("model", "¿Recuerdas el modelo de los equipos {label}?"),
    ):
        for index, eq in enumerate(extracted.get("equipment", [])):
            key = f"equipment.{index}.{field}"
            if is_missing(eq.get(field)) and key not in unknown_fields:
                return key, question.format(label=eq.get("modality") or "observados")

    return None, None


def _apply_explicit_unknown(extracted: dict, question_key: Optional[str]) -> dict:
    if not question_key:
        return extracted
    if question_key == "customer_name":
        extracted["customer_name"] = "Desconocido"
        return extracted
    parts = question_key.split(".")
    if len(parts) == 3 and parts[0] == "equipment":
        try:
            idx = int(parts[1])
            field = parts[2]
            eq = extracted.get("equipment", [])[idx]
        except (ValueError, IndexError):
            return extracted
        if field in {"manufacturer", "model"}:
            eq[field] = None
        elif field == "estimated_age":
            eq[field] = None
    return extracted


def _confidence_numeric(eq: dict, times_reported: int = 1, state: Optional[str] = None) -> float:
    weights = {
        "modality": 0.25,
        "manufacturer": 0.20,
        "model": 0.20,
        "estimated_age": 0.20,
        "quantity": 0.15,
    }
    score = 0.0
    if is_missing(eq.get("modality")):
        return 0.0
    for field, weight in weights.items():
        value = eq.get(field)
        if not is_missing(value):
            score += weight
    state = state or eq.get("state") or "Reportado"
    if state == "Confirmado":
        score += 0.08
    elif state == "Estimado":
        score -= 0.08
    elif state == "Desconocido":
        score -= 0.12
    # Repetition by the same reporter is not independent evidence.
    return round(max(0.05, min(score, 0.99)), 2)


def _confidence_label(score: float) -> str:
    if score <= 0:
        return "Unknown"
    if score >= 0.78:
        return "High"
    if score >= 0.5:
        return "Medium"
    return "Low"


def _utc_iso(value):
    return value.replace(tzinfo=datetime.timezone.utc).isoformat() if value else None


def _serialize_equipment(eq: Equipment) -> dict:
    stale_days = None
    stale = False
    if eq.last_verified:
        stale_days = max((utcnow() - eq.last_verified).days, 0)
        stale = stale_days > STALE_DAYS
    return {
        "id": eq.id,
        "modality": eq.modality,
        "quantity": eq.quantity,
        "manufacturer": eq.manufacturer,
        "model": eq.model,
        "serial_number": eq.serial_number,
        "estimated_age": eq.estimated_age,
        "confidence": eq.confidence,
        "confidence_score": round(float(eq.confidence_score or 0), 2),
        "observation_state": eq.observation_state or "Reportado",
        "times_reported": eq.times_reported or 1,
        "last_updated": _utc_iso(eq.last_updated),
        "last_verified": _utc_iso(eq.last_verified),
        "source": eq.source or "Conversación",
        "photo_path": eq.photo_path,
        "is_stale": stale,
        "freshness": "unknown" if stale_days is None else ("fresh" if stale_days < FRESH_DAYS else "aging" if stale_days <= STALE_DAYS else "stale"),
        "needs_revalidation": stale_days is None or stale_days >= FRESH_DAYS,
        "days_since_verification": stale_days,
    }


@app.post("/api/observations/process")
async def process_observation(request: ObservationRequest, db: Session = Depends(get_db)):
    """QVAC extrae y conversa; todavía NO persiste Installed Base."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="La observación está vacía")

    session = None
    if request.session_id:
        session = db.query(PendingSession).filter(PendingSession.id == request.session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Sesión de captura no encontrada")

    previous_data, unknown_fields, previous_question = _session_payload(session)
    if previous_data and len(json.dumps(previous_data)) > 8000:
        raise HTTPException(413, "Captura demasiado extensa. Confirma lo disponible o comienza otra visita.")
    if session and session.rounds >= MAX_CAPTURE_ROUNDS:
        raise HTTPException(409, "Se alcanzó el límite de turnos. Confirma lo disponible o descarta la captura.")

    if session and is_explicit_unknown_reply(text):
        extracted = _apply_explicit_unknown(previous_data or {}, previous_question)
        if previous_question and previous_question not in unknown_fields:
            unknown_fields.append(previous_question)
    else:
        try:
            new_extracted = await extract_observation(text, context={**previous_data, "_question_key": previous_question} if previous_data else None)
        except QvacNotReadyError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "qvac_not_ready",
                    "message": "QVAC local no disponible. Ejecuta qvac_warmup.py para diagnosticar.",
                    "requirement": "on-device inference",
                },
            )
        extracted = merge_extracted_data(previous_data, new_extracted) if previous_data else new_extracted

    raw_texts = (json.loads(session.raw_texts) if session else []) + [text]
    rounds = (session.rounds if session else 0) + 1

    question_key, follow_up = _next_question(extracted, unknown_fields)
    must_have_missing = is_missing(extracted.get("customer_name")) or not extracted.get("equipment")
    should_ask = follow_up is not None and (rounds <= MAX_FOLLOW_UP_ROUNDS or (must_have_missing and rounds < MAX_CAPTURE_ROUNDS))

    session_id = session.id if session else str(uuid.uuid4())
    payload = {
        "data": extracted,
        "unknown_fields": unknown_fields,
        "question_key": question_key if should_ask else None,
    }

    if session:
        session.accumulated_data = json.dumps(payload, ensure_ascii=False)
        session.raw_texts = json.dumps(raw_texts, ensure_ascii=False)
        session.rounds = rounds
    else:
        session = PendingSession(
            id=session_id,
            accumulated_data=json.dumps(payload, ensure_ascii=False),
            raw_texts=json.dumps(raw_texts, ensure_ascii=False),
            submitted_by=request.submitted_by,
            rounds=rounds,
            source="Conversación",
        )
        db.add(session)
    db.commit()

    if should_ask:
        return {
            "status": "needs_more_info",
            "message": "La observación necesita un dato de alto valor.",
            "follow_up": follow_up,
            "session_id": session_id,
            "extracted_so_far": extracted,
            "inference": get_ai_runtime_status(),
        }

    if must_have_missing:
        return {
            "status": "error",
            "message": "Falta identificar al menos el cliente y un equipo antes de confirmar.",
            "session_id": session_id,
            "extracted": extracted,
        }

    return {
        "status": "ready_for_confirmation",
        "message": "Revisa los datos antes de guardarlos localmente.",
        "session_id": session_id,
        "extracted": extracted,
        "raw_observation": " | ".join(raw_texts),
        "inference": get_ai_runtime_status(),
    }


@app.post("/api/observations/confirm")
def confirm_observation(request: ConfirmObservationRequest, db: Session = Depends(get_db)):
    """Persiste la sesión confirmada en SQLite y actualiza Installed Base."""
    session = db.query(PendingSession).filter(PendingSession.id == request.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sesión de captura no encontrada")

    extracted, _, _ = _session_payload(session)
    raw_texts = json.loads(session.raw_texts)
    if not extracted or is_missing(extracted.get("customer_name")) or not extracted.get("equipment"):
        raise HTTPException(status_code=400, detail="La captura no tiene datos mínimos para confirmar")

    possible_customers = db.query(Customer).filter(func.fold(Customer.name) == fold(extracted["customer_name"])).all()
    compatible = [c for c in possible_customers if all(
        is_missing(getattr(c, f)) or is_missing(extracted.get(f)) or fold(getattr(c, f)) == fold(extracted[f])
        for f in ("city", "country"))]
    if len(compatible) > 1:
        raise HTTPException(409, "Hay varios clientes con ese nombre. Añade ciudad y país para distinguirlos.")
    customer = compatible[0] if compatible else None
    if not customer:
        customer = Customer(
            name=extracted["customer_name"],
            city=extracted.get("city"),
            country=extracted.get("country"),
        )
        db.add(customer)
        db.flush()
    else:
        if is_missing(customer.city) and not is_missing(extracted.get("city")):
            customer.city = extracted.get("city")
        if is_missing(customer.country) and not is_missing(extracted.get("country")):
            customer.country = extracted.get("country")

    now = utcnow()
    base_state = infer_observation_state(raw_texts)
    observation_score = 0.0
    saved_equipment = []
    duplicate_candidates = []

    observation = Observation(
        raw_text=" | ".join(raw_texts),
        submitted_by=session.submitted_by,
        submitted_at=now,
        last_verified=now,
        confidence="Medium",
        confidence_score=0.5,
        observation_state=base_state,
        source=session.source or "Conversación",
        photo_path=session.photo_path,
        customer_id=customer.id,
        structured_json=json.dumps(extracted, ensure_ascii=False),
    )
    db.add(observation)
    db.flush()

    for eq_data in extracted.get("equipment", []):
        modality = eq_data.get("modality") or "Desconocido"
        manufacturer = eq_data.get("manufacturer")
        model = eq_data.get("model")

        candidates = db.query(Equipment).filter(
            Equipment.customer_id == customer.id,
            func.lower(Equipment.modality) == modality.lower(),
        ).all()
        matches = []
        for cand in candidates:
            if cand.id in [e.id for e in saved_equipment]:
                continue
            conflicts = any(not is_missing(eq_data.get(f)) and not is_missing(getattr(cand, f))
                            and fold(eq_data[f]) != fold(getattr(cand, f))
                            for f in ("manufacturer", "model", "serial_number"))
            age = eq_data.get("estimated_age")
            age_conflict = age is not None and cand.estimated_age is not None and abs(age-cand.estimated_age) > 1
            quantity_conflict = eq_data.get("quantity") is not None and cand.quantity is not None and eq_data["quantity"] != cand.quantity
            serial = eq_data.get("serial_number")
            serial_match = bool(serial and fold(serial) == fold(cand.serial_number))
            anchor_match = (not is_missing(manufacturer) and fold(manufacturer) == fold(cand.manufacturer)
                            and ((not is_missing(model) and fold(model) == fold(cand.model))
                                 or (age is not None and cand.estimated_age is not None and not age_conflict)))
            if not conflicts and not age_conflict and not quantity_conflict and (serial_match or anchor_match):
                matches.append(cand)
        match = matches[0] if len(matches) == 1 else None

        incoming_state = eq_data.get("state") or base_state
        if incoming_state == "Confirmado":
            incoming_state = "Reportado"

        if match:
            duplicate_candidates.append(match.id)
            if is_missing(match.manufacturer) and not is_missing(manufacturer):
                match.manufacturer = manufacturer
            if is_missing(match.model) and not is_missing(model):
                match.model = model
            if is_missing(match.serial_number) and not is_missing(eq_data.get("serial_number")):
                match.serial_number = eq_data.get("serial_number")
            if eq_data.get("estimated_age") is not None:
                match.estimated_age = eq_data.get("estimated_age")
            if eq_data.get("quantity") is not None:
                match.quantity = eq_data["quantity"]
            match.times_reported = (match.times_reported or 1) + 1
            match.last_updated = now
            match.last_verified = now
            prior_reporters = db.query(Observation.submitted_by).join(
                ObservationEquipment, Observation.id == ObservationEquipment.observation_id
            ).filter(ObservationEquipment.equipment_id == match.id).all()
            reporter = fold(session.submitted_by)
            independent = reporter not in {"field employee", "", "unknown", "desconocido"} and any(
                fold(row[0]) not in {reporter, "field employee", "", "unknown", "desconocido"} for row in prior_reporters)
            match.observation_state = "Estimado" if "Estimado" in {incoming_state, match.observation_state} else (
                "Confirmado" if independent or match.observation_state == "Confirmado" else incoming_state)
            match.source = session.source or match.source or "Conversación"
            score = _confidence_numeric(_serialize_equipment(match), match.times_reported, match.observation_state)
            match.confidence_score = score
            match.confidence = _confidence_label(score)
            target = match
        else:
            score = _confidence_numeric(eq_data, 1, incoming_state)
            target = Equipment(
                modality=modality,
                manufacturer=manufacturer,
                model=model,
                serial_number=eq_data.get("serial_number"),
                quantity=eq_data.get("quantity"),
                estimated_age=eq_data.get("estimated_age"),
                confidence=_confidence_label(score),
                confidence_score=score,
                observation_state=incoming_state,
                customer_id=customer.id,
                times_reported=1,
                last_updated=now,
                last_verified=now,
                source=session.source or "Conversación",
                photo_path=session.photo_path,
            )
            db.add(target)
            db.flush()
            target.quantity = eq_data.get("quantity")

        if observation.equipment_id is None:
            observation.equipment_id = target.id
        db.add(ObservationEquipment(observation_id=observation.id, equipment_id=target.id))
        saved_equipment.append(target)
        observation_score += float(target.confidence_score or 0)

    observation.confidence_score = round(observation_score / max(len(saved_equipment), 1), 2)
    observation.confidence = _confidence_label(observation.confidence_score)
    observation.is_duplicate_update = "Yes" if duplicate_candidates else "No"
    if saved_equipment and all(eq.observation_state == "Confirmado" for eq in saved_equipment):
        observation.observation_state = "Confirmado"

    db.delete(session)
    db.commit()
    db.refresh(observation)

    return {
        "status": "success",
        "message": "Observación confirmada y guardada localmente",
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "city": customer.city,
            "country": customer.country,
        },
        "equipment": [_serialize_equipment(eq) for eq in saved_equipment],
        "observation_confidence": observation.confidence,
        "observation_confidence_score": observation.confidence_score,
        "observation_state": observation.observation_state,
        "contains_duplicate_update": bool(duplicate_candidates),
        "duplicate_equipment_ids": duplicate_candidates,
        "observation_id": observation.id,
    }


@app.delete("/api/observations/pending/{session_id}")
def cancel_pending_observation(session_id: str, db: Session = Depends(get_db)):
    session = db.query(PendingSession).filter(PendingSession.id == session_id).first()
    if session:
        db.delete(session)
        db.commit()
    return {"status": "cancelled", "session_id": session_id}

# ==================== ENDPOINTS DE CONSULTA ====================

@app.get("/api/customers", response_model=List[CustomerResponse])
def get_customers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Obtener todos los clientes con paginación."""
    if skip < 0 or not 1 <= limit <= 1000:
        raise HTTPException(422, "skip >= 0; limit entre 1 y 1000")
    return db.query(Customer).offset(skip).limit(limit).all()

@app.patch("/api/customers/{customer_id}/country")
def update_customer_country(
    customer_id: int,
    request: CustomerCountryUpdate,
    db: Session = Depends(get_db)
):
    """
    Asigna o corrige manualmente el país de un cliente.

    La región no se guarda en Customer porque se deriva automáticamente
    del país en la interfaz geográfica.
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    country = normalize_country(request.country)

    if not country:
        raise HTTPException(
            status_code=400,
            detail="Debes indicar un país"
        )

    customer.country = country
    db.commit()
    db.refresh(customer)

    return {
        "status": "success",
        "message": "País del cliente actualizado",
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "city": customer.city,
            "country": customer.country,
        }
    }


@app.get("/api/geography/catalog")
def get_geography_catalog():
    return country_catalog()


@app.get("/api/geography/custom-countries")
def get_custom_countries(db: Session = Depends(get_db)):
    """
    Devuelve los países/territorios que el usuario agregó manualmente y
    la región a la que fueron asignados.
    """
    rows = db.query(CountryRegion).order_by(
        CountryRegion.region.asc(),
        CountryRegion.country_name.asc()
    ).all()

    return [
        {
            "id": row.id,
            "country_name": row.country_name,
            "region": row.region,
        }
        for row in rows
    ]


@app.post("/api/geography/custom-countries")
def add_custom_country(
    request: CountryRegionRequest,
    db: Session = Depends(get_db)
):
    """
    Agrega un país/territorio manualmente a una región.

    Si el país ya había sido creado manualmente, actualiza su región.
    """
    allowed_regions = {
        "Norteamérica",
        "Centroamérica y Caribe",
        "Sudamérica",
        "Europa",
        "Asia",
        "Medio Oriente",
        "África",
        "Oceanía",
        "Otras regiones",
    }

    country_name = normalize_country(request.country_name)
    region = request.region.strip()

    if not country_name:
        raise HTTPException(
            status_code=400,
            detail="El nombre del país no puede estar vacío"
        )

    if region not in allowed_regions:
        raise HTTPException(
            status_code=400,
            detail="La región seleccionada no es válida"
        )

    existing = db.query(CountryRegion).filter(
        func.fold(CountryRegion.country_name) == fold(country_name)
    ).first()

    if existing:
        existing.country_name = country_name
        existing.region = region
        db.commit()
        db.refresh(existing)

        return {
            "status": "success",
            "message": "País actualizado",
            "country": {
                "id": existing.id,
                "country_name": existing.country_name,
                "region": existing.region,
            }
        }

    row = CountryRegion(
        country_name=country_name,
        region=region
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "status": "success",
        "message": "País agregado",
        "country": {
            "id": row.id,
            "country_name": row.country_name,
            "region": row.region,
        }
    }


@app.get("/api/customers/{customer_id}")
def get_customer_detail(customer_id: int, db: Session = Depends(get_db)):
    """Obtener detalle de un cliente con su equipamiento."""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    equipment = db.query(Equipment).filter(Equipment.customer_id == customer_id).all()
    observations = db.query(Observation).filter(Observation.customer_id == customer_id).all()

    # Se serializa a diccionarios planos en vez de devolver los objetos ORM
    # directamente: sin un response_model, FastAPI intenta volcar el __dict__
    # de SQLAlchemy (incluyendo _sa_instance_state) y la respuesta falla.
    return {
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "city": customer.city,
            "country": customer.country,
            "created_at": _utc_iso(customer.created_at)
        },
        "equipment": [
            {
                "id": eq.id,
                "modality": eq.modality,
                "manufacturer": eq.manufacturer,
                **_serialize_equipment(eq)
            } for eq in equipment
        ],
        "observations": [
            {
                "id": obs.id,
                "raw_text": obs.raw_text,
                "submitted_by": obs.submitted_by,
                "submitted_at": _utc_iso(obs.submitted_at),
                "confidence": obs.confidence,
                "confidence_score": obs.confidence_score,
                "observation_state": obs.observation_state or "Reportado",
                "last_verified": _utc_iso(obs.last_verified),
                "source": obs.source or "Conversación",
                "photo_path": obs.photo_path,
                "equipment_id": obs.equipment_id,
                "is_duplicate_update": obs.is_duplicate_update
            } for obs in observations
        ]
    }

@app.get("/api/equipment", response_model=List[EquipmentResponse])
def get_all_equipment(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Obtener todo el equipamiento registrado con paginación."""
    if skip < 0 or not 1 <= limit <= 1000:
        raise HTTPException(422, "skip >= 0; limit entre 1 y 1000")
    return db.query(Equipment).offset(skip).limit(limit).all()

@app.get("/api/equipment/by-modality")
def get_equipment_by_modality(db: Session = Depends(get_db)):
    """Agrupar equipamiento por modalidad."""
    results = db.query(
        Equipment.modality,
        func.sum(Equipment.quantity).label("total")
    ).group_by(Equipment.modality).all()

    return [{"modality": r[0], "total": r[1]} for r in results]

@app.get("/api/equipment/by-country")
def get_equipment_by_country(db: Session = Depends(get_db)):
    """Agrupar equipamiento por país."""
    results = db.query(
        Customer.country,
        func.sum(Equipment.quantity).label("total")
    ).join(Equipment).group_by(Customer.country).all()

    return [{"country": r[0] or "Desconocido", "total": r[1]} for r in results]

@app.get("/api/observations", response_model=List[ObservationResponse])
def get_observations(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Obtener todas las observaciones con paginación."""
    if skip < 0 or not 1 <= limit <= 1000:
        raise HTTPException(422, "skip >= 0; limit entre 1 y 1000")
    return db.query(Observation).offset(skip).limit(limit).all()


@app.delete("/api/observations/{observation_id}")
def delete_observation(observation_id: int, db: Session = Depends(get_db)):
    """
    Elimina una observación específica del historial.

    Importante:
    - Solo borra el registro histórico de la observación.
    - No elimina el cliente.
    - No elimina ni modifica el equipamiento instalado.
    """
    observation = db.query(Observation).filter(
        Observation.id == observation_id
    ).first()

    if not observation:
        raise HTTPException(status_code=404, detail="Observación no encontrada")

    customer_id = observation.customer_id

    db.query(ObservationEquipment).filter(ObservationEquipment.observation_id == observation_id).delete()
    db.delete(observation)
    db.commit()

    return {
        "status": "success",
        "message": "Observación eliminada del historial",
        "observation_id": observation_id,
        "customer_id": customer_id,
        "equipment_preserved": True,
    }




@app.get("/api/installed-base")
def get_installed_base(
    customer: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    modality: Optional[str] = None,
    manufacturer: Optional[str] = None,
    min_age: Optional[float] = None,
    max_age: Optional[float] = None,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Installed Base local con filtros, sin servicios externos."""
    query = db.query(Equipment, Customer).join(Customer, Equipment.customer_id == Customer.id)
    if customer:
        query = query.filter(Customer.name.ilike(f"%{customer}%"))
    if country:
        query = query.filter(func.fold(Customer.country).contains(fold(normalize_country(country)), autoescape=True))
    if city:
        query = query.filter(Customer.city.ilike(f"%{city}%"))
    if modality:
        query = query.filter(Equipment.modality == normalize_modality(modality))
    if manufacturer:
        query = query.filter(Equipment.manufacturer.ilike(f"%{manufacturer}%"))
    if min_age is not None:
        query = query.filter(Equipment.estimated_age >= min_age)
    if max_age is not None:
        query = query.filter(Equipment.estimated_age <= max_age)
    if state:
        query = query.filter(Equipment.observation_state.ilike(state))

    rows = query.order_by(Customer.name.asc(), Equipment.modality.asc()).all()
    return {
        "count": len(rows),
        "items": [
            {
                "customer": {
                    "id": customer_row.id,
                    "name": customer_row.name,
                    "city": customer_row.city,
                    "country": customer_row.country,
                },
                **_serialize_equipment(eq),
            }
            for eq, customer_row in rows
        ],
    }


def _fold_query(text: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )


@app.post("/api/queries/local")
def query_local_dataset(request: NaturalQueryRequest, db: Session = Depends(get_db)):
    """
    Consulta determinística sobre SQLite. No es IA y no envía el dataset a QVAC
    ni a ningún servicio externo. Cubre las consultas de demo más importantes.
    """
    import re
    q = _fold_query(request.query)
    remaining = q
    query = db.query(Equipment, Customer).join(Customer, Equipment.customer_id == Customer.id)
    applied = []

    hits = equipment_mentions(q)
    if len({h["modality"] for h in hits}) > 1:
        raise HTTPException(422, "Consulta una modalidad a la vez.")
    if hits:
        canonical = hits[0]["modality"]
        query = query.filter(Equipment.modality == canonical)
        applied.append(f"modalidad={canonical}")
        for hit in reversed(hits):
            remaining = remaining[:hit["start"]] + " " + remaining[hit["end"]:]
    age_match = re.search(rf"(?:mas de|mayores? de|superior(?:es)? a)\s+({NUMBER_PATTERN})\s+anos", q)
    if age_match:
        age = number(age_match[1])
        query = query.filter(Equipment.estimated_age > age)
        applied.append(f"antigüedad>{age:g}")
        remaining = remaining.replace(age_match[0], " ")
    elif re.search(r"\b(anos|edad|antiguedad)\b", q):
        raise HTTPException(422, "Para filtrar edad usa 'más de siete años' o 'más de 7 años'.")
    found_brands = {fold(brand): brand for brand, in db.query(Equipment.manufacturer).distinct().all()
                    if brand and re.search(rf"\b{re.escape(fold(brand))}\b", q)}
    if len(found_brands) > 1:
        raise HTTPException(422, "Consulta una marca a la vez.")
    for brand_key, brand in found_brands.items():
        query = query.filter(func.fold(Equipment.manufacturer) == brand_key)
        applied.append(f"marca={brand}")
        remaining = re.sub(rf"\b{re.escape(brand_key)}\b", " ", remaining)
    countries = dict(country_aliases())
    countries.update({fold(c): c for c, in db.query(Customer.country).distinct().all() if c})
    found_countries = {canonical for alias, canonical in countries.items() if re.search(rf"\b{re.escape(alias)}\b", q)}
    if len(found_countries) > 1:
        raise HTTPException(422, "Consulta un país a la vez.")
    if found_countries:
        country_name = found_countries.pop()
        query = query.filter(func.fold(Customer.country) == fold(country_name))
        applied.append(f"país={country_name}")
        for alias in sorted(countries, key=len, reverse=True):
            if countries[alias] == country_name:
                remaining = re.sub(rf"\b{re.escape(alias)}\b", " ", remaining)
    if re.search(r"\b(no|excepto|sin|menos|entre)\b", q):
        raise HTTPException(422, "Esta consulta no admite negaciones ni rangos. Usa los filtros de Installed Base.")
    if not applied:
        raise HTTPException(422, "No reconocí filtros. Prueba: MR en Brasil de más de siete años.")

    if "confirmado" in q:
        query = query.filter(Equipment.observation_state == "Confirmado")
        applied.append("estado=Confirmado")
    elif "estimado" in q:
        query = query.filter(Equipment.observation_state == "Estimado")
        applied.append("estado=Estimado")

    # Never silently ignore unknown location, brand or other constraints.
    remaining = re.sub(
        r"\b(?:que|cuales|muestrame|mostrar|dame|ver|lista|listar|los|las|el|la|un|una|"
        r"clientes?|equipos?|sistemas?|registrados?|tienen|tiene|hay|con|en|de|del|"
        r"pais|marca|y|confirmados?|estimados?)\b", " ", remaining)
    if re.search(r"\w", remaining):
        raise HTTPException(422, "Hay filtros que no reconozco. Usa una modalidad, marca registrada, país o 'más de siete años'; para otros criterios usa Installed Base.")

    rows = query.order_by(Customer.name.asc(), Equipment.estimated_age.desc()).limit(100).all()
    items = [
        {
            "customer": customer_row.name,
            "city": customer_row.city,
            "country": customer_row.country,
            **_serialize_equipment(eq),
        }
        for eq, customer_row in rows
    ]
    return {
        "query": request.query,
        "parser": "local_deterministic",
        "cloud": False,
        "applied_filters": applied,
        "count": len(items),
        "items": items,
    }


@app.get("/api/opportunities")
def get_renewal_opportunities(min_age: float = REFRESH_OPPORTUNITY_YEARS, db: Session = Depends(get_db)):
    """Señales de renovación conservadoras; no afirman necesidad de reemplazo."""
    rows = db.query(Equipment, Customer).join(Customer).filter(
        Equipment.estimated_age.isnot(None),
        Equipment.estimated_age >= min_age,
    ).order_by(Equipment.estimated_age.desc()).all()

    return {
        "threshold_years": min_age,
        "items": [
            {
                "customer": customer.name,
                "city": customer.city,
                "country": customer.country,
                "equipment": _serialize_equipment(eq),
                "message": "Potencial oportunidad de renovación. Requiere validación comercial.",
            }
            for eq, customer in rows
        ],
    }


@app.get("/api/capabilities")
def capabilities():
    status = get_ai_runtime_status()
    return {
        "qvac_text_extraction": {
            "implemented": True,
            "ready": bool(status.get("qvac_ready")),
            "provider": "QVAC",
            "location": "on_device",
        },
        "voice": {
            "implemented": True,
            "ready": bool(status.get("qvac_asr_ready")),
            "model": status.get("qvac_asr_model"),
            "load_mode": "lazy",
        },
        "vision": {
            "implemented": False,
            "reason": "La cámara/vision QVAC queda preparada como fase posterior y no se presenta como funcional.",
        },
        "local_queries": {"implemented": True, "engine": "deterministic_sqlite"},
    }

# ==================== ENDPOINTS DE ANALYTICS ====================

@app.get("/api/analytics/dashboard")
def get_dashboard(db: Session = Depends(get_db)):
    """Obtener datos agregados para el dashboard."""
    total_customers = db.query(Customer).count()
    # "total_equipment" representa unidades físicas instaladas, no filas de la tabla.
    total_equipment = db.query(
        func.coalesce(func.sum(Equipment.quantity), 0)
    ).scalar() or 0
    total_equipment_categories = db.query(Equipment).count()
    total_observations = db.query(Observation).count()

    # Equipos por modalidad
    by_modality = db.query(
        Equipment.modality,
        func.sum(Equipment.quantity).label("total")
    ).group_by(Equipment.modality).all()

    # Equipos por país
    by_country = db.query(
        Customer.country,
        func.sum(Equipment.quantity).label("total")
    ).join(Equipment).group_by(Customer.country).all()

    # Equipos por edad estimada: sumar unidades, no contar registros/modalidades.
    by_age = db.query(
        Equipment.estimated_age,
        func.sum(Equipment.quantity).label("total")
    ).group_by(Equipment.estimated_age).all()

    # Clientes con información incompleta
    incomplete_customers = db.query(Customer).filter(
        or_(Customer.city.is_(None), Customer.country.is_(None), ~Customer.equipment.any(),
            Customer.equipment.any(or_(Equipment.manufacturer.is_(None), Equipment.model.is_(None),
                                        Equipment.estimated_age.is_(None), Equipment.quantity.is_(None))))
    ).count()

    freshness = dict.fromkeys(("fresh", "aging", "stale", "unknown"), 0)
    for eq in db.query(Equipment).all():
        freshness[_serialize_equipment(eq)["freshness"]] += 1
    return {
        "total_customers": total_customers,
        "total_equipment": int(total_equipment),
        "total_equipment_categories": total_equipment_categories,
        "total_observations": total_observations,
        "by_modality": [{"modality": r[0], "total": r[1]} for r in by_modality],
        "by_country": [{"country": r[0] or "Desconocido", "total": r[1]} for r in by_country],
        "by_age": [{"age": r[0], "total": r[1]} for r in by_age],
        "incomplete_customers": incomplete_customers,
        "unknown_quantity_records": db.query(Equipment).filter(Equipment.quantity.is_(None)).count(),
        "confidence": [{"level": level, "total": count} for level, count in db.query(Equipment.confidence, func.count(Equipment.id)).group_by(Equipment.confidence).all()],
        "freshness": freshness,
        "aging_technology_customers": db.query(Customer).filter(Customer.equipment.any(Equipment.estimated_age >= REFRESH_OPPORTUNITY_YEARS)).count(),
        "recently_updated_customers": db.query(Customer).filter(Customer.equipment.any(Equipment.last_verified >= utcnow() - datetime.timedelta(days=FRESH_DAYS))).count(),
    }

# ==================== ENDPOINTS PARA DATOS DE PRUEBA ====================

@app.post("/api/seed")
def seed_database(db: Session = Depends(get_db)):
    """Cargar datos de ejemplo para la demostración."""

    if db.query(Customer).count() > 0:
        return {"seeded": False, "message": "La base ya contiene clientes; el seed solo se aplica a una base vacía."}
    # Entirely fictional facilities/cities/brands/models; countries are real catalog labels.
    rows = [
        ("Hospital Demo Aurora", "Ciudad Aurora", "Brasil", "MR", "LumenWorks", "DemoMag A1", 2, 9, "Estimado", 45),
        ("Hospital Demo Pacífico", "Ciudad Pacífica", "Panamá", "CT", "NovaMed", "DemoScan C2", 1, 7, "Reportado", 200),
        ("Clínica Demo Horizonte", "Villa Horizonte", "Perú", "Ultrasound", None, None, 3, None, "Reportado", 400),
        ("Hospital Demo Boreal", "Ciudad Boreal", "Canadá", "Patient Monitor", "LumenWorks", None, 6, 2, "Reportado", 20),
        ("Centro Demo Alba", "Ciudad Alba", "España", "Mammography", "NovaMed", "DemoMammo M1", 1, 8, "Reportado", 90),
        ("Clínica Demo Sakura", "Ciudad Sakura", "Japón", "PET/CT", "Demo Imaging", "DemoFusion P1", 1, 3, "Reportado", 15),
        ("Hospital Demo Dunas", "Ciudad Dunas", "Emiratos Árabes Unidos", "Ventilator", None, None, None, None, "Reportado", None),
        ("Centro Demo Acacia", "Ciudad Acacia", "Kenia", "X-Ray", "Demo Imaging", None, 2, 12, "Estimado", 500),
        ("Hospital Demo Coral", "Ciudad Coral", "Australia", "ECG", "NovaMed", "DemoPulse E1", 2, 0, "Reportado", 30),
    ]
    try:
        for name, city, country, modality, brand, model, quantity, age, state, days in rows:
            customer = Customer(name=name, city=city, country=country)
            db.add(customer)
            db.flush()
            observed = utcnow() - datetime.timedelta(days=days) if days is not None else None
            data = {"modality": modality, "manufacturer": brand, "model": model,
                    "quantity": quantity, "estimated_age": age, "state": state}
            score = _confidence_numeric(data, state=state)
            eq = Equipment(customer_id=customer.id, modality=modality, manufacturer=brand, model=model,
                           quantity=quantity, estimated_age=age, observation_state=state,
                           confidence_score=score, confidence=_confidence_label(score),
                           source="Dataset sintético", last_updated=observed, last_verified=observed)
            db.add(eq)
            db.flush()
            # SQLAlchemy defaults apply to None on insert; explicitly preserve unknown evidence.
            eq.quantity, eq.last_updated, eq.last_verified = quantity, observed, observed
            obs = Observation(customer_id=customer.id, equipment_id=eq.id,
                              raw_text=f"Dataset sintético: {name}, {modality}. Sin datos reales de clientes.",
                              submitted_by="Demo Seed", source="Dataset sintético", submitted_at=observed or utcnow(),
                              last_verified=observed, observation_state=state,
                              confidence_score=score, confidence=_confidence_label(score),
                              structured_json=json.dumps({"customer_name": name, "city": city, "country": country, "equipment": [data]}, ensure_ascii=False))
            db.add(obs)
            db.flush()
            db.add(ObservationEquipment(observation_id=obs.id, equipment_id=eq.id))
        db.commit()
        return {"seeded": True, "message": "Dataset sintético cargado: 9 clientes ficticios.", "customers": len(rows)}
    except Exception:
        db.rollback()
        raise HTTPException(500, "No se pudo cargar el dataset; no se guardaron cambios parciales.")
