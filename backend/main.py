from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text as sql_text
from typing import List, Optional
from pydantic import BaseModel
from contextlib import asynccontextmanager
import sys
import os

# Agregar la carpeta actual al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import json
import uuid
import datetime

from database import get_db, engine
from models import Base, Customer, Equipment, Observation, PendingSession, CountryRegion
from schemas import CustomerCreate, CustomerResponse, EquipmentCreate, EquipmentResponse, ObservationCreate, ObservationResponse
from ai import (
    extract_observation,
    get_follow_up_question,
    merge_extracted_data,
    get_equipment_confidence,
    get_confidence_score,
    is_missing,
    ai_is_configured,
    get_ai_runtime_status,
    initialize_qvac,
    shutdown_qvac,
    QvacNotReadyError,
)

MAX_FOLLOW_UP_ROUNDS = 2  # cuántas veces como máximo se pregunta antes de guardar con lo que haya

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
    }
    observation_migrations = {
        "observation_state": "VARCHAR DEFAULT 'Reportado'",
        "is_duplicate_update": "VARCHAR DEFAULT 'No'",
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
                    "last_updated = COALESCE(last_updated, CURRENT_TIMESTAMP)"
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
                    "is_duplicate_update = COALESCE(is_duplicate_update, 'No')"
                )
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

    yield

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== ESQUEMAS ====================

class ObservationRequest(BaseModel):
    text: str
    submitted_by: Optional[str] = "Field Employee"
    session_id: Optional[str] = None  # si viene, es la respuesta a una pregunta de seguimiento


class CountryRegionRequest(BaseModel):
    country_name: str
    region: str


class CustomerCountryUpdate(BaseModel):
    country: str

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
    """Reconoce respuestas breves que indican que el dato no se conoce."""
    normalized = (text or "").strip().lower().strip(" .,!?:;")
    return normalized in {
        "no sé", "no se", "no lo sé", "no lo se",
        "no se sabe", "no conozco", "desconocido", "unknown",
    }


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

# ==================== ENDPOINTS DE OBSERVACIONES ====================

@app.post("/api/observations/process")
async def process_observation(request: ObservationRequest, db: Session = Depends(get_db)):
    """
    Endpoint principal: recibe un texto en lenguaje natural sobre una
    observación de campo y lo convierte en datos estructurados.

    Soporta una conversación de varios turnos: si falta información
    crítica (cliente o equipos), devuelve una pregunta de seguimiento y un
    session_id. El siguiente mensaje del usuario debe reenviar ese
    session_id para que la respuesta se combine con lo ya capturado, en
    vez de tratarse como una observación nueva y separada.
    """
    text = request.text

    # 1. Recuperar la sesión antes de extraer para que una respuesta corta
    #    (ej. "Siemens") pueda interpretarse usando el contexto previo.
    session: Optional[PendingSession] = None
    if request.session_id:
        session = db.query(PendingSession).filter(PendingSession.id == request.session_id).first()

    previous_data = json.loads(session.accumulated_data) if session else None
    try:
        new_extracted = await extract_observation(
            text,
            context=previous_data
        )
    except QvacNotReadyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "qvac_not_ready",
                "message": str(exc),
                "requirement": "on-device inference",
            },
        )

    # 2. Si hay una sesión pendiente, combinar con lo ya acumulado
    raw_texts: List[str] = [text]
    rounds = 1

    if session:
        previous_data = json.loads(session.accumulated_data)
        extracted = merge_extracted_data(previous_data, new_extracted)
        raw_texts = json.loads(session.raw_texts) + [text]
        rounds = session.rounds + 1
    else:
        extracted = new_extracted

    # 3. Validar si falta información crítica (cliente o al menos un equipo)
    customer_missing = is_missing(extracted.get("customer_name"))
    equipment_missing = len(extracted.get("equipment", [])) == 0

    # 4. Decidir si hace falta seguir preguntando
    follow_up = get_follow_up_question(extracted)
    needs_more_info = (customer_missing or equipment_missing or follow_up not in (
        None, "¡Gracias! La información ha sido registrada correctamente."
    ))

    # Si el usuario responde explícitamente que no conoce el dato, conservar
    # el valor incompleto y guardar en vez de insistir con la misma pregunta.
    if session and is_explicit_unknown_reply(text):
        needs_more_info = False

    if needs_more_info and rounds <= MAX_FOLLOW_UP_ROUNDS:
        session_id = session.id if session else str(uuid.uuid4())
        session_data = {
            "id": session_id,
            "accumulated_data": json.dumps(extracted),
            "raw_texts": json.dumps(raw_texts),
            "submitted_by": request.submitted_by,
            "rounds": rounds,
        }
        if session:
            session.accumulated_data = session_data["accumulated_data"]
            session.raw_texts = session_data["raw_texts"]
            session.rounds = rounds
        else:
            session = PendingSession(**session_data)
            db.add(session)
        db.commit()

        return {
            "status": "needs_more_info",
            "message": "Necesito un poco más de información antes de guardar esto.",
            "follow_up": follow_up,
            "session_id": session_id,
            "extracted_so_far": extracted
        }

    if customer_missing:
        # Se agotaron los intentos y seguimos sin cliente: no hay nada que guardar
        if session:
            db.delete(session)
            db.commit()
        return {
            "status": "error",
            "message": "No se pudo identificar el cliente en el texto.",
            "extracted": extracted
        }

    # 5. Buscar o crear el cliente
    customer = db.query(Customer).filter(
        Customer.name == extracted["customer_name"]
    ).first()

    if not customer:
        customer = Customer(
            name=extracted["customer_name"],
            city=extracted.get("city"),
            country=extracted.get("country")
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
    else:
        # Completar city/country si el cliente ya existía pero le faltaban
        if is_missing(customer.city) and not is_missing(extracted.get("city")):
            customer.city = extracted.get("city")
        if is_missing(customer.country) and not is_missing(extracted.get("country")):
            customer.country = extracted.get("country")
        db.commit()

    # 6. Crear la observación (confianza real según completitud de los datos)
    observation_confidence = get_confidence_score(extracted)
    base_observation_state = infer_observation_state(raw_texts)
    observation = Observation(
        raw_text=" | ".join(raw_texts),
        submitted_by=request.submitted_by,
        confidence=observation_confidence,
        observation_state=base_observation_state,
        customer_id=customer.id
    )
    db.add(observation)
    db.commit()
    db.refresh(observation)

    # 7. Procesar cada equipo extraído, detectando posibles duplicados
    equipment_list = []
    any_duplicate = False

    for eq_data in extracted.get("equipment", []):
        modality = eq_data.get("modality") or "Desconocido"
        manufacturer = eq_data.get("manufacturer")

        # Buscar equipo ya existente de este cliente con la misma modalidad.
        # Si ambos tienen fabricante, debe coincidir; si alguno no lo sabe,
        # se considera de todas formas un posible duplicado a revisar.
        existing_query = db.query(Equipment).filter(
            Equipment.customer_id == customer.id,
            Equipment.modality.ilike(modality)
        )
        candidates = existing_query.all()
        match = None
        incoming_model = eq_data.get("model")

        for cand in candidates:
            # Si ambos fabricantes son conocidos y no coinciden, son equipos distintos.
            if manufacturer and cand.manufacturer:
                if cand.manufacturer.strip().casefold() != str(manufacturer).strip().casefold():
                    continue

            # Si ambos modelos son conocidos y no coinciden, tampoco se fusionan.
            if incoming_model and cand.model:
                if cand.model.strip().casefold() != str(incoming_model).strip().casefold():
                    continue

            match = cand
            break

        if match:
            # Actualizar el equipo existente en vez de crear uno duplicado
            if is_missing(match.manufacturer) and not is_missing(manufacturer):
                match.manufacturer = manufacturer
            if is_missing(match.model) and not is_missing(eq_data.get("model")):
                match.model = eq_data.get("model")
            if not is_missing(eq_data.get("estimated_age")):
                match.estimated_age = eq_data.get("estimated_age")
            if not is_missing(eq_data.get("quantity")):
                match.quantity = max(match.quantity or 1, eq_data.get("quantity"))
            match.times_reported = (match.times_reported or 1) + 1
            match.last_updated = datetime.datetime.utcnow()
            match.observation_state = "Confirmado"
            match.confidence = get_equipment_confidence({
                "manufacturer": match.manufacturer,
                "model": match.model,
                "estimated_age": match.estimated_age
            })
            db.commit()
            db.refresh(match)
            equipment_list.append(match)
            any_duplicate = True

            if not observation.equipment_id:
                observation.equipment_id = match.id
        else:
            equipment = Equipment(
                modality=modality,
                manufacturer=manufacturer,
                model=eq_data.get("model"),
                quantity=eq_data.get("quantity") or 1,
                estimated_age=eq_data.get("estimated_age"),
                confidence=get_equipment_confidence(eq_data),
                observation_state=base_observation_state,
                customer_id=customer.id,
                times_reported=1
            )
            db.add(equipment)
            db.commit()
            db.refresh(equipment)
            equipment_list.append(equipment)

            if not observation.equipment_id:
                observation.equipment_id = equipment.id

    observation.is_duplicate_update = "Yes" if any_duplicate else "No"
    observation.observation_state = infer_observation_state(
        raw_texts,
        confirmed_by_repeat=any_duplicate,
    )
    db.commit()

    # 8. Limpiar la sesión pendiente, ya que la observación quedó completa
    if session:
        db.delete(session)
        db.commit()

    return {
        "status": "success",
        "message": "Observación procesada correctamente",
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "city": customer.city,
            "country": customer.country
        },
        "equipment": [
            {
                "id": eq.id,
                "modality": eq.modality,
                "quantity": eq.quantity,
                "manufacturer": eq.manufacturer,
                "model": eq.model,
                "estimated_age": eq.estimated_age,
                "confidence": eq.confidence,
                "observation_state": eq.observation_state or "Reportado",
                "times_reported": eq.times_reported
            } for eq in equipment_list
        ],
        "observation_confidence": observation_confidence,
        "observation_state": observation.observation_state,
        "contains_duplicate_update": any_duplicate,
        "observation_id": observation.id
    }

# ==================== ENDPOINTS DE CONSULTA ====================

@app.get("/api/customers", response_model=List[CustomerResponse])
def get_customers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Obtener todos los clientes con paginación."""
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

    country = request.country.strip()

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

    country_name = request.country_name.strip()
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
        func.lower(CountryRegion.country_name) == country_name.lower()
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
            "created_at": customer.created_at
        },
        "equipment": [
            {
                "id": eq.id,
                "modality": eq.modality,
                "manufacturer": eq.manufacturer,
                "model": eq.model,
                "quantity": eq.quantity,
                "estimated_age": eq.estimated_age,
                "confidence": eq.confidence,
                "observation_state": eq.observation_state or "Reportado",
                "times_reported": eq.times_reported,
                "last_updated": eq.last_updated
            } for eq in equipment
        ],
        "observations": [
            {
                "id": obs.id,
                "raw_text": obs.raw_text,
                "submitted_by": obs.submitted_by,
                "submitted_at": obs.submitted_at,
                "confidence": obs.confidence,
                "observation_state": obs.observation_state or "Reportado",
                "equipment_id": obs.equipment_id,
                "is_duplicate_update": obs.is_duplicate_update
            } for obs in observations
        ]
    }

@app.get("/api/equipment", response_model=List[EquipmentResponse])
def get_all_equipment(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Obtener todo el equipamiento registrado con paginación."""
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

    db.delete(observation)
    db.commit()

    return {
        "status": "success",
        "message": "Observación eliminada del historial",
        "observation_id": observation_id,
        "customer_id": customer_id,
        "equipment_preserved": True,
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
        ~Customer.equipment.any(Equipment.manufacturer.isnot(None))
    ).count()
    
    return {
        "total_customers": total_customers,
        "total_equipment": int(total_equipment),
        "total_equipment_categories": total_equipment_categories,
        "total_observations": total_observations,
        "by_modality": [{"modality": r[0], "total": r[1]} for r in by_modality],
        "by_country": [{"country": r[0] or "Desconocido", "total": r[1]} for r in by_country],
        "by_age": [{"age": r[0], "total": r[1]} for r in by_age],
        "incomplete_customers": incomplete_customers
    }

# ==================== ENDPOINTS PARA DATOS DE PRUEBA ====================

@app.post("/api/seed")
def seed_database(db: Session = Depends(get_db)):
    """Cargar datos de ejemplo para la demostración."""
    
    # Verificar si ya hay datos
    if db.query(Customer).count() > 0:
        return {"message": "La base de datos ya tiene datos. No se cargaron datos duplicados."}
    
    try:
        # Datos de ejemplo
        customers_data = [
            {"name": "Hospital Alpha", "city": "São Paulo", "country": "Brazil"},
            {"name": "Hospital Beta", "city": "Rio de Janeiro", "country": "Brazil"},
            {"name": "Hospital Gamma", "city": "Buenos Aires", "country": "Argentina"},
            {"name": "Hospital Delta", "city": "Santiago", "country": "Chile"},
            {"name": "Hospital Epsilon", "city": "Bogotá", "country": "Colombia"},
            {"name": "Hospital Zeta", "city": "Lima", "country": "Peru"},
            {"name": "Hospital Eta", "city": "Montevideo", "country": "Uruguay"},
            {"name": "Hospital Theta", "city": "Quito", "country": "Ecuador"},
        ]
        
        equipment_data = [
            {"customer": "Hospital Alpha", "modality": "MR", "quantity": 3, "estimated_age": 8, "manufacturer": "Siemens"},
            {"customer": "Hospital Alpha", "modality": "CT", "quantity": 2, "estimated_age": 5, "manufacturer": "GE"},
            {"customer": "Hospital Alpha", "modality": "Ultrasound", "quantity": 4, "estimated_age": None},
            {"customer": "Hospital Beta", "modality": "MR", "quantity": 1, "estimated_age": 12, "manufacturer": "Philips"},
            {"customer": "Hospital Beta", "modality": "CT", "quantity": 3, "estimated_age": 7, "manufacturer": "Siemens"},
            {"customer": "Hospital Gamma", "modality": "Ultrasound", "quantity": 6, "estimated_age": 3},
            {"customer": "Hospital Gamma", "modality": "PET", "quantity": 1, "estimated_age": 4, "manufacturer": "GE"},
            {"customer": "Hospital Delta", "modality": "MR", "quantity": 2, "estimated_age": 6, "manufacturer": "Siemens"},
            {"customer": "Hospital Delta", "modality": "CT", "quantity": 2, "estimated_age": None},
            {"customer": "Hospital Epsilon", "modality": "Ultrasound", "quantity": 3, "estimated_age": 2},
            {"customer": "Hospital Epsilon", "modality": "MR", "quantity": 1, "estimated_age": 10, "manufacturer": "GE"},
            {"customer": "Hospital Zeta", "modality": "CT", "quantity": 2, "estimated_age": 4, "manufacturer": "Siemens"},
            {"customer": "Hospital Zeta", "modality": "MR", "quantity": 1, "estimated_age": 9, "manufacturer": "Philips"},
            {"customer": "Hospital Eta", "modality": "Ultrasound", "quantity": 2, "estimated_age": 1},
            {"customer": "Hospital Theta", "modality": "PET", "quantity": 1, "estimated_age": 5, "manufacturer": "GE"},
        ]
        
        # Crear clientes
        created_customers = {}
        for c in customers_data:
            customer = Customer(**c)
            db.add(customer)
            db.commit()
            db.refresh(customer)
            created_customers[c["name"]] = customer
        
        # Crear equipamiento
        for e in equipment_data:
            customer = created_customers.get(e["customer"])
            if customer:
                equipment = Equipment(
                    customer_id=customer.id,
                    modality=e["modality"],
                    quantity=e["quantity"],
                    estimated_age=e.get("estimated_age"),
                    manufacturer=e.get("manufacturer"),
                    confidence="High",
                    observation_state="Reportado"
                )
                db.add(equipment)
        
        db.commit()
        
        return {"message": "Datos de ejemplo cargados correctamente."}
        
    except Exception as e:
        db.rollback()
        return {"message": f"Error al cargar datos: {str(e)}"}