"""
Pydantic Schemas for Person Watchlist and Facial Biometric Quality Gates.
"""

from datetime import datetime
from typing import Optional, List
import uuid
from pydantic import BaseModel, ConfigDict, Field


class FaceQualityMetrics(BaseModel):
    """Diagnostic quality metrics returned from the 5-gatekeeper check."""
    face_detected: bool = True
    sharpness_score: float = 0.0
    face_resolution: List[int] = Field(default_factory=lambda: [0, 0])
    yaw_ratio: float = 0.0
    roll_angle_deg: float = 0.0
    brightness_mean: float = 0.0
    is_frontal: bool = True
    quality_passed: bool = True
    rejection_reason: Optional[str] = None


class PersonWatchlistCreate(BaseModel):
    """Schema for registering an individual."""
    name: str = Field(..., min_length=2, max_length=120)
    category: str = Field(..., pattern="^(wanted|missing|suspect)$")
    status: str = Field("active", pattern="^(active|resolved)$")


class PersonWatchlistUpdate(BaseModel):
    """Schema for updating person record details or status."""
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    category: Optional[str] = Field(None, pattern="^(wanted|missing|suspect)$")
    status: Optional[str] = Field(None, pattern="^(active|resolved)$")


class PersonWatchlistResponse(BaseModel):
    """Output schema for a person watchlist entry."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str
    status: str
    photo_path: Optional[str] = None
    has_embedding: bool = False
    embedding_dim: Optional[int] = None
    created_at: datetime
    quality_metrics: Optional[FaceQualityMetrics] = None
