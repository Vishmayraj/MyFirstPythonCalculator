"""
Audit Log API router — global audit trail view.
GET /api/v1/audit
"""

from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import require_role
from shared.db.models import Camera as CameraModel
from shared.db.models import StatusHistory as StatusHistoryModel
from shared.db.models import User as UserModel
from shared.db.session import get_db

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditItemResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    camera_name: str
    changed_field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_by_user: Optional[str] = None
    changed_at: str


@router.get("", response_model=list[AuditItemResponse])
def get_global_audit_log(
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    # AuditReport1.md #8: the global audit trail is camera change history
    # across every department, which is a step above what a read-only
    # "viewer" account (no department, read-only per Project_Context.md
    # §3) should be able to pull in one request. Scoped the same way the
    # rest of this codebase scopes department-sensitive data: dept_admin
    # and operator can see it (both are staff who act on cameras day to
    # day), further narrowed to their own department below when they have
    # one; viewer is excluded from this endpoint entirely (403).
    current_user: UserModel = Depends(require_role("dept_admin", "operator")),
):
    """Retrieve system audit trail, ordered by changed_at DESC.

    Scoped to the caller's department when they have one (matching the
    same `current_user.department_id` convention used for camera
    mutations in cameras.py) - a department-scoped dept_admin/operator
    only sees history for cameras in their own department. Callers with
    no department_id (global admins/operators) see the full cross-
    department trail, same as before.
    """
    query = db.query(StatusHistoryModel).options(
        joinedload(StatusHistoryModel.camera),
    )
    if current_user.department_id:
        query = query.filter(
            StatusHistoryModel.camera.has(
                CameraModel.department_id == current_user.department_id
            )
        )
    rows = query.order_by(StatusHistoryModel.changed_at.desc()).limit(limit).all()

    # Gather unique changed_by user IDs for efficient lookup
    user_ids = {r.changed_by for r in rows if r.changed_by is not None}
    users_by_id = {}
    if user_ids:
        users = db.query(UserModel).filter(UserModel.id.in_(user_ids)).all()
        users_by_id = {u.id: u.username for u in users}

    result = []
    for r in rows:
        camera_name = r.camera.name if r.camera else "Unknown Camera"
        changed_by_username = users_by_id.get(r.changed_by, "System / Direct DB") if r.changed_by else "System / Direct DB"

        result.append(
            AuditItemResponse(
                id=r.id,
                camera_id=r.camera_id,
                camera_name=camera_name,
                changed_field=r.changed_field,
                old_value=r.old_value,
                new_value=r.new_value,
                changed_by_user=changed_by_username,
                changed_at=r.changed_at.isoformat(),
            )
        )

    return result
