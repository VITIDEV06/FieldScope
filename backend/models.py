from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base
import datetime


def utcnow():
    """SQLite stores UTC without a timezone; APIs attach UTC on serialization."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    city = Column(String)
    country = Column(String)
    created_at = Column(DateTime, default=utcnow)

    equipment = relationship("Equipment", back_populates="customer")
    observations = relationship("Observation", back_populates="customer")


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True, index=True)
    modality = Column(String, nullable=False)
    manufacturer = Column(String)
    model = Column(String)
    serial_number = Column(String)
    quantity = Column(Integer, default=1)
    estimated_age = Column(Float)
    confidence = Column(String, default="Medium")
    confidence_score = Column(Float, default=0.5)
    observation_state = Column(String, default="Reportado")
    customer_id = Column(Integer, ForeignKey("customers.id"))
    times_reported = Column(Integer, default=1)
    last_updated = Column(DateTime, default=utcnow, onupdate=utcnow)
    last_verified = Column(DateTime, default=utcnow)
    source = Column(String, default="Conversación")
    photo_path = Column(String)

    customer = relationship("Customer", back_populates="equipment")
    observations = relationship("Observation", back_populates="equipment")


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, index=True)
    raw_text = Column(Text)
    submitted_by = Column(String, default="Field Employee")
    submitted_at = Column(DateTime, default=utcnow)
    last_verified = Column(DateTime, default=utcnow)
    confidence = Column(String, default="Medium")
    confidence_score = Column(Float, default=0.5)
    observation_state = Column(String, default="Reportado")
    source = Column(String, default="Conversación")
    photo_path = Column(String)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    is_duplicate_update = Column(String, default="No")
    structured_json = Column(Text)

    customer = relationship("Customer", back_populates="observations")
    equipment = relationship("Equipment", back_populates="observations")


class PendingSession(Base):
    """Estado local de una captura aún no confirmada por el usuario."""
    __tablename__ = "pending_sessions"

    id = Column(String, primary_key=True)
    accumulated_data = Column(Text)
    raw_texts = Column(Text)
    submitted_by = Column(String, default="Field Employee")
    rounds = Column(Integer, default=1)
    source = Column(String, default="Conversación")
    photo_path = Column(String)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class CountryRegion(Base):
    __tablename__ = "country_regions"

    id = Column(Integer, primary_key=True, index=True)
    country_name = Column(String, nullable=False, unique=True, index=True)
    region = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class ObservationEquipment(Base):
    """All equipment supported by an observation; deletion preserves the assets."""
    __tablename__ = "observation_equipment"
    observation_id = Column(Integer, ForeignKey("observations.id", ondelete="CASCADE"), primary_key=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), primary_key=True)
