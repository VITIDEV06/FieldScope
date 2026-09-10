from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# Esquemas para Customer
class CustomerBase(BaseModel):
    name: str
    city: Optional[str] = None
    country: Optional[str] = None

class CustomerCreate(CustomerBase):
    pass

class CustomerResponse(CustomerBase):  # ✅ Cambiado de Customer a CustomerResponse
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

# Esquemas para Equipment
class EquipmentBase(BaseModel):
    modality: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    quantity: int = 1
    estimated_age: Optional[float] = None
    confidence: str = "Medium"

class EquipmentCreate(EquipmentBase):
    customer_id: int

class EquipmentResponse(EquipmentBase):
    id: int
    customer_id: int
    observation_state: str = "Reportado"
    times_reported: int = 1
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

# Esquemas para Observation
class ObservationBase(BaseModel):
    raw_text: str
    submitted_by: str = "Field Employee"
    confidence: str = "Medium"
    observation_state: str = "Reportado"

class ObservationCreate(ObservationBase):
    customer_id: int
    equipment_id: Optional[int] = None

class ObservationResponse(ObservationBase):
    id: int
    submitted_at: datetime
    customer_id: int
    equipment_id: Optional[int] = None
    is_duplicate_update: str = "No"

    class Config:
        from_attributes = True

# Esquema para extracción de IA
class ExtractedEquipment(BaseModel):
    modality: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    quantity: int = 1
    estimated_age: Optional[float] = None

class ExtractedObservation(BaseModel):
    customer_name: str
    city: Optional[str] = None
    country: Optional[str] = None
    equipment: List[ExtractedEquipment]
    confidence: str = "Medium"