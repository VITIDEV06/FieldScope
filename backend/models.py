from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base
import datetime

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    city = Column(String)
    country = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    equipment = relationship("Equipment", back_populates="customer")
    observations = relationship("Observation", back_populates="customer")

class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True, index=True)
    modality = Column(String, nullable=False)
    manufacturer = Column(String)
    model = Column(String)
    quantity = Column(Integer, default=1)
    estimated_age = Column(Float)
    confidence = Column(String, default="Medium")
    observation_state = Column(String, default="Reportado")  # Confirmado/Reportado/Estimado/Desconocido
    customer_id = Column(Integer, ForeignKey("customers.id"))
    times_reported = Column(Integer, default=1)
    last_updated = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    customer = relationship("Customer", back_populates="equipment")
    observations = relationship("Observation", back_populates="equipment")

class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, index=True)
    raw_text = Column(Text)
    submitted_by = Column(String, default="Field Employee")
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow)
    confidence = Column(String, default="Medium")
    observation_state = Column(String, default="Reportado")
    customer_id = Column(Integer, ForeignKey("customers.id"))
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    is_duplicate_update = Column(String, default="No")  # "Yes"/"No": si actualizó equipo existente en vez de crear uno nuevo

    customer = relationship("Customer", back_populates="observations")
    equipment = relationship("Equipment", back_populates="observations")

class PendingSession(Base):
    """
    Guarda el estado acumulado de una observación mientras el usuario responde
    preguntas de seguimiento, antes de confirmarse y guardarse como definitiva.
    """
    __tablename__ = "pending_sessions"

    id = Column(String, primary_key=True)  # UUID de sesión
    accumulated_data = Column(Text)  # JSON con la info extraída hasta el momento
    raw_texts = Column(Text)  # JSON list de todos los mensajes de texto de la sesión
    submitted_by = Column(String, default="Field Employee")
    rounds = Column(Integer, default=1)  # cuántas veces se ha preguntado
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class CountryRegion(Base):
    """
    Países/territorios agregados manualmente desde Geografía.
    Permite que el usuario indique en qué región debe vivir un país que
    no exista en el catálogo del frontend.
    """
    __tablename__ = "country_regions"

    id = Column(Integer, primary_key=True, index=True)
    country_name = Column(String, nullable=False, unique=True, index=True)
    region = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
