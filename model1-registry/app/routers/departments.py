"""
Department API router — read-only list endpoint.
GET /api/v1/departments
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from shared.db.models import User as UserModel
from shared.db.models import Camera as CameraModel
from shared.db.models import Department as DepartmentModel
from shared.db.models import User as UserModel
from shared.db.session import get_db
from shared.schemas.department import Department as DepartmentSchema

from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/v1/departments", tags=["departments"])


@router.get("", response_model=list[DepartmentSchema])
def list_departments(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """List all departments with a camera count per department."""
    rows = (
        db.query(
            DepartmentModel,
            func.count(CameraModel.id).label("camera_count"),
        )
        .outerjoin(
            CameraModel,
            (CameraModel.department_id == DepartmentModel.id)
            & (CameraModel.is_active == True),  # noqa: E712
        )
        .group_by(DepartmentModel.id)
        .order_by(DepartmentModel.name)
        .all()
    )
    result = []
    for dept, count in rows:
        dept_dict = {
            "id": dept.id,
            "name": dept.name,
            "category": dept.category,
            "created_at": dept.created_at,
            "camera_count": count,
        }
        result.append(dept_dict)
    return result
