"""
Pydantic schemas for the vehicles_watchlist table.

Supports:
  - Strict Indian License Plate format validation (Standard + BH series)
  - Meaningful description length validation
  - Category validation ('stolen', 'wanted', 'blacklisted')
  - Status validation ('active', 'resolved')
"""

import re
import uuid
from datetime import date, datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


# Regular expressions for Indian License Plates:
# 1. Standard State format (e.g. GJ01AB1234, DL3C1234, MH12A1234, GJ011234)
# 2. Bharat Series format (e.g. 22BH1234AA)
# 3. Military / Diplomatic / EV standard variants
STANDARD_PLATE_REGEX = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")
BHARAT_SERIES_REGEX = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")


class VehicleWatchlistBase(BaseModel):
    plate_number: str = Field(..., description="Vehicle license plate number (e.g. GJ01AB1234)")
    category: Literal["stolen", "wanted", "blacklisted"] = Field(..., description="Watchlist category")
    reported_date: Optional[date] = Field(default=None, description="Date the incident was reported")
    department_id: Optional[uuid.UUID] = Field(default=None, description="Reporting department UUID")
    description: str = Field(..., min_length=10, max_length=500, description="Incident details / vehicle make, model, color")
    status: Literal["active", "resolved"] = Field(default="active", description="Watchlist status")

    @field_validator("plate_number")
    @classmethod
    def validate_and_normalize_plate(cls, v: str) -> str:
        """Validate and normalize Indian registration plate numbers."""
        if not v or not v.strip():
            raise ValueError("License plate number is required.")
        
        # Remove all whitespace, hyphens, and convert to uppercase
        clean_plate = re.sub(r"[\s\-\.]+", "", v.strip().upper())

        # Check against Indian plate patterns
        if not (STANDARD_PLATE_REGEX.match(clean_plate) or BHARAT_SERIES_REGEX.match(clean_plate)):
            raise ValueError(
                f"Invalid Indian license plate format: '{v}'. "
                "Expected standard format (e.g., GJ01AB1234, DL04C1234) or Bharat Series (e.g., 22BH1234AA)."
            )
        return clean_plate

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        clean_desc = v.strip() if v else ""
        if len(clean_desc) < 10:
            raise ValueError("Incident description must be at least 10 characters long with meaningful details.")
        return clean_desc


class VehicleWatchlistCreate(VehicleWatchlistBase):
    """POST /api/v1/watchlist/vehicles — Create new watchlist entry."""
    pass


class VehicleWatchlistUpdate(BaseModel):
    """PATCH /api/v1/watchlist/vehicles/{id} — Partial update."""
    plate_number: Optional[str] = None
    category: Optional[Literal["stolen", "wanted", "blacklisted"]] = None
    reported_date: Optional[date] = None
    department_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    status: Optional[Literal["active", "resolved"]] = None

    @field_validator("plate_number")
    @classmethod
    def validate_and_normalize_plate(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean_plate = re.sub(r"[\s\-\.]+", "", v.strip().upper())
            if not (STANDARD_PLATE_REGEX.match(clean_plate) or BHARAT_SERIES_REGEX.match(clean_plate)):
                raise ValueError(
                    f"Invalid Indian license plate format: '{v}'. "
                    "Expected standard format (e.g., GJ01AB1234, DL04C1234) or Bharat Series (e.g., 22BH1234AA)."
                )
            return clean_plate
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean_desc = v.strip()
            if len(clean_desc) < 10:
                raise ValueError("Incident description must be at least 10 characters long.")
            return clean_desc
        return v


class VehicleWatchlistResponse(VehicleWatchlistBase):
    """Watchlist object representation with timestamps and department info."""
    id: uuid.UUID
    created_at: datetime
    department_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
