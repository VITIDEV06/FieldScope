from pydantic import BaseModel, ConfigDict, Field, field_serializer
from typing import Optional, List
from datetime import datetime, timezone


class APIModel(BaseModel):
    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_utc(self, value):
        return value.replace(tzinfo=timezone.utc).isoformat() if isinstance(value, datetime) else value


class CustomerBase(APIModel):
    name: str
    city: Optional[str] = None
    country: Optional[str] = None


class CustomerCreate(CustomerBase):
    pass


class CustomerResponse(CustomerBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EquipmentBase(APIModel):
    modality: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    quantity: Optional[int] = Field(default=None, ge=1, le=10000)
    estimated_age: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    confidence: str = "Medium"
    confidence_score: float = 0.5
    source: str = "Conversación"
    photo_path: Optional[str] = None


class EquipmentCreate(EquipmentBase):
    customer_id: int


class EquipmentResponse(EquipmentBase):
    id: int
    customer_id: int
    observation_state: str = "Reportado"
    times_reported: int = 1
    last_updated: Optional[datetime] = None
    last_verified: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ObservationBase(APIModel):
    raw_text: str
    submitted_by: str = "Field Employee"
    confidence: str = "Medium"
    confidence_score: float = 0.5
    observation_state: str = "Reportado"
    source: str = "Conversación"
    photo_path: Optional[str] = None


class ObservationCreate(ObservationBase):
    customer_id: int
    equipment_id: Optional[int] = None


class ObservationResponse(ObservationBase):
    id: int
    submitted_at: datetime
    last_verified: Optional[datetime] = None
    customer_id: int
    equipment_id: Optional[int] = None
    is_duplicate_update: str = "No"

    model_config = ConfigDict(from_attributes=True)


class ExtractedEquipment(BaseModel):
    modality: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    quantity: Optional[int] = Field(default=None, ge=1, le=10000)
    estimated_age: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    state: str = "Reportado"


class ExtractedObservation(BaseModel):
    customer_name: str
    city: Optional[str] = None
    country: Optional[str] = None
    equipment: List[ExtractedEquipment]
    confidence: str = "Medium"
