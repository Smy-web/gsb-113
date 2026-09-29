from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Dict, Any
from datetime import datetime


class IceCoreData(BaseModel):
    depth_meters: float = Field(ge=0)
    ice_temperature: float
    bubble_density: float
    co2_concentration: float
    methane_concentration: float
    oxygen18_ratio: float
    dust_concentration: float
    estimated_age: Optional[float] = None


class TranscriptSegment(BaseModel):
    speaker: str
    speaker_role: str
    start_time: float
    end_time: float
    text: str
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def _end_not_before_start(self):
        if self.end_time < self.start_time:
            raise ValueError('end_time must not be earlier than start_time')
        return self


class ClimateEvent(BaseModel):
    event_type: str
    description: str
    estimated_year: Optional[int] = None
    depth_reference: Optional[float] = None
    confidence: float = Field(ge=0, le=1)


class DrillingLog(BaseModel):
    log_id: str
    timestamp: datetime
    location: str
    ice_core_data: IceCoreData
    transcript: List[TranscriptSegment]
    climate_events: List[ClimateEvent]
    summary: Optional[str] = None
    sample_allocation: Optional[Dict[str, Any]] = None
    transmitted: bool = False

    @field_validator('log_id')
    @classmethod
    def _log_id_not_blank(cls, value):
        if not value.strip():
            raise ValueError('log_id must be non-empty and not pure whitespace')
        return value


class AudioUploadResponse(BaseModel):
    audio_id: str
    status: str
    message: str


class SummaryResponse(BaseModel):
    log_id: str
    summary: str
    sample_allocation: Dict[str, Any]


class TransmissionStatus(BaseModel):
    log_id: str
    transmitted: bool
    transmission_time: Optional[datetime] = None
    error_message: Optional[str] = None
