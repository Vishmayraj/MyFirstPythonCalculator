"""
Model 2 — Watchlist API Router
Endpoints:
  - GET    /api/v1/watchlist/vehicles       (List / Filter watchlist entries)
  - POST   /api/v1/watchlist/vehicles       (Create new vehicle watchlist record)
  - GET    /api/v1/watchlist/vehicles/{id}  (Get single watchlist record)
  - PATCH  /api/v1/watchlist/vehicles/{id}  (Update watchlist record / status)
  - DELETE /api/v1/watchlist/vehicles/{id}  (Delete watchlist record & cascade alerts)
"""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.auth.dependencies import require_role
from shared.db.models import User as UserModel

from shared.db.models import (
    VehicleWatchlist as VehicleWatchlistModel,
    Department as DepartmentModel,
    Alert as AlertModel,
)
from shared.db.session import get_db
from shared.schemas.watchlist import (
    VehicleWatchlistCreate,
    VehicleWatchlistUpdate,
    VehicleWatchlistResponse,
)

router = APIRouter(prefix="/api/v1/watchlist/vehicles", tags=["vehicle-watchlist"])


def _format_watchlist_response(item: VehicleWatchlistModel) -> dict:
    """Helper to format ORM model to dictionary with department name."""
    return {
        "id": item.id,
        "plate_number": item.plate_number,
        "category": item.category,
        "reported_date": item.reported_date,
        "department_id": item.department_id,
        "department_name": item.department.name if item.department else None,
        "description": item.description,
        "status": item.status,
        "created_at": item.created_at,
    }


@router.get("", response_model=List[VehicleWatchlistResponse])
def list_watchlist_vehicles(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (active/resolved)"),
    category: Optional[str] = Query(None, description="Filter by category (stolen/wanted/blacklisted)"),
    plate_number: Optional[str] = Query(None, description="Search by plate number"),
    department_id: Optional[uuid.UUID] = Query(None, description="Filter by department UUID"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """
    Retrieve all vehicle watchlist entries with optional filtering.
    """
    query = db.query(VehicleWatchlistModel)

    if status_filter:
        query = query.filter(VehicleWatchlistModel.status == status_filter.lower())
    if category:
        query = query.filter(VehicleWatchlistModel.category == category.lower())
    if plate_number:
        clean_search = "".join(plate_number.strip().upper().split())
        query = query.filter(VehicleWatchlistModel.plate_number.ilike(f"%{clean_search}%"))
    if department_id:
        query = query.filter(VehicleWatchlistModel.department_id == department_id)

    items = query.order_by(desc(VehicleWatchlistModel.created_at)).offset(offset).limit(limit).all()
    return [_format_watchlist_response(item) for item in items]


@router.post("", response_model=VehicleWatchlistResponse, status_code=status.HTTP_201_CREATED)
def create_watchlist_vehicle(
    payload: VehicleWatchlistCreate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """
    Add a new vehicle to the watchlist (stolen, wanted, or blacklisted).
    Validates Indian plate format and ensures meaningful description.
    """
    # Check if active entry for this plate already exists
    existing = (
        db.query(VehicleWatchlistModel)
        .filter(
            VehicleWatchlistModel.plate_number == payload.plate_number,
            VehicleWatchlistModel.status == "active",
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active watchlist entry already exists for vehicle plate '{payload.plate_number}' (ID: {existing.id}).",
        )

    # Verify department_id if provided
    if payload.department_id:
        dept = db.query(DepartmentModel).filter(DepartmentModel.id == payload.department_id).first()
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Department with ID '{payload.department_id}' not found.",
            )

    new_entry = VehicleWatchlistModel(
        plate_number=payload.plate_number,
        category=payload.category,
        reported_date=payload.reported_date,
        department_id=payload.department_id,
        description=payload.description,
        status=payload.status,
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)

    return _format_watchlist_response(new_entry)


@router.get("/{id}", response_model=VehicleWatchlistResponse)
def get_watchlist_vehicle(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """
    Get details of a specific watchlist entry by UUID.
    """
    item = db.query(VehicleWatchlistModel).filter(VehicleWatchlistModel.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle watchlist entry with ID '{id}' not found.",
        )
    return _format_watchlist_response(item)


@router.patch("/{id}", response_model=VehicleWatchlistResponse)
def update_watchlist_vehicle(
    id: uuid.UUID,
    payload: VehicleWatchlistUpdate,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """
    Update details or status of a watchlist entry (e.g. resolve a case).
    """
    item = db.query(VehicleWatchlistModel).filter(VehicleWatchlistModel.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle watchlist entry with ID '{id}' not found.",
        )

    update_data = payload.model_dump(exclude_unset=True)

    # Verify department_id if changing
    if "department_id" in update_data and update_data["department_id"] is not None:
        dept = db.query(DepartmentModel).filter(DepartmentModel.id == update_data["department_id"]).first()
        if not dept:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Department with ID '{update_data['department_id']}' not found.",
            )

    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return _format_watchlist_response(item)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist_vehicle(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """
    Delete a vehicle watchlist entry permanently and cascade associated alerts.
    """
    item = db.query(VehicleWatchlistModel).filter(VehicleWatchlistModel.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vehicle watchlist entry with ID '{id}' not found.",
        )

    # Cascade delete associated alerts to prevent Foreign Key violation
    db.query(AlertModel).filter(AlertModel.watchlist_id == id).delete(synchronize_session=False)

    db.delete(item)
    db.commit()
    return None
