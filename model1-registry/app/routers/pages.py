"""
Page routes — HTML views served via Jinja2 templates.

These are the user-facing pages, separate from the /api/v1 JSON routers.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.auth.dependencies import get_optional_current_user
from shared.db.models import Camera as CameraModel
from shared.db.models import Department as DeptModel
from shared.db.models import District as DistModel
from shared.db.models import User as UserModel
from shared.db.session import get_db

router = APIRouter(tags=["pages"])


# ── Login Page ──────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if user:
        return RedirectResponse(url="/", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request, name="login.html", context={"user": None}
    )


# ── Map (home page) ────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
def map_page(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request, name="map.html", context={"user": user}
    )


# ── Live Feeds Video Matrix ─────────────────────────────────────


@router.get("/live", response_class=HTMLResponse)
def live_feeds_page(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request, name="live.html", context={"user": user}
    )


# ── Camera list ─────────────────────────────────────────────────


@router.get("/cameras", response_class=HTMLResponse)
def cameras_list_page(
    request: Request,
    department_id: Optional[str] = Query(None),
    district_id: Optional[str] = Query(None),
    connectivity_status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    departments = db.query(DeptModel).order_by(DeptModel.name).all()
    districts = db.query(DistModel).order_by(DistModel.name).all()

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="cameras_list.html",
        context={
            "user": user,
            "departments": departments,
            "districts": districts,
            "selected_department": department_id or "",
            "selected_district": district_id or "",
            "selected_status": connectivity_status or "",
        },
    )


# ── Camera list partial (HTMX tbody swap) ──────────────────────


@router.get("/cameras/table", response_class=HTMLResponse)
def cameras_table_partial(
    request: Request,
    department_id: Optional[str] = Query(None),
    district_id: Optional[str] = Query(None),
    connectivity_status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    from geoalchemy2.shape import to_shape

    q = (
        db.query(CameraModel)
        .options(
            joinedload(CameraModel.department),
            joinedload(CameraModel.district),
        )
        .filter(CameraModel.is_active == True)  # noqa: E712
    )
    if department_id:
        q = q.filter(CameraModel.department_id == department_id)
    if district_id:
        q = q.filter(CameraModel.district_id == district_id)
    if connectivity_status:
        q = q.filter(CameraModel.connectivity_status == connectivity_status)

    cameras = q.order_by(CameraModel.name).all()

    # Pre-process locations for template
    camera_rows = []
    for cam in cameras:
        loc_str = None
        if cam.location is not None:
            try:
                point = to_shape(cam.location)
                loc_str = f"{point.y:.4f}, {point.x:.4f}"
            except Exception:
                pass
        camera_rows.append({"cam": cam, "location_str": loc_str})

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="cameras_table_partial.html",
        context={"user": user, "camera_rows": camera_rows},
    )


# ── Camera form (create / edit) ────────────────────────────────


@router.get("/cameras/new", response_class=HTMLResponse)
def camera_new_form(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    # Only dept_admins can ever POST /api/v1/cameras (require_role("dept_admin")
    # on the API side) — matching that here means we never render a form that
    # is guaranteed to fail on submit for anyone else.
    if not user or user.role != "dept_admin":
        return RedirectResponse(url="/cameras", status_code=302)

    departments = db.query(DeptModel).order_by(DeptModel.name).all()
    if user.department_id:
        # A department-scoped admin can only ever create within their own
        # department (create_camera 403s / now defaults otherwise) — don't
        # offer choices that will fail.
        departments = [d for d in departments if d.id == user.department_id]
    districts = db.query(DistModel).order_by(DistModel.name).all()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="camera_form.html",
        context={
            "user": user,
            "camera": None,
            "departments": departments,
            "districts": districts,
            "errors": {},
            "editable": True,
        },
    )


@router.get("/cameras/{camera_id}/edit", response_class=HTMLResponse)
def camera_edit_form(
    camera_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user or user.role != "dept_admin":
        return RedirectResponse(url="/cameras", status_code=302)

    cam = (
        db.query(CameraModel)
        .options(
            joinedload(CameraModel.department),
            joinedload(CameraModel.district),
        )
        .filter(CameraModel.id == camera_id)
        .first()
    )
    if not cam:
        return HTMLResponse(status_code=404, content="Camera not found")

    # Mirrors the 403 condition in PATCH/DELETE /api/v1/cameras/{id} exactly.
    # If this dept_admin can't save changes to this camera, say so up front
    # instead of letting them fill out a form that will fail on submit.
    editable = not (user.department_id and cam.department_id != user.department_id)

    departments = db.query(DeptModel).order_by(DeptModel.name).all()
    if user.department_id and editable:
        departments = [d for d in departments if d.id == user.department_id]
    districts = db.query(DistModel).order_by(DistModel.name).all()

    # Extract lat/lon from PostGIS geography
    lat, lon = None, None
    if cam.location is not None:
        try:
            from geoalchemy2.shape import to_shape

            point = to_shape(cam.location)
            lat, lon = point.y, point.x
        except Exception:
            pass

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="camera_form.html",
        context={
            "user": user,
            "camera": cam,
            "camera_lat": lat,
            "camera_lon": lon,
            "departments": departments,
            "districts": districts,
            "errors": {},
            "editable": editable,
        },
    )


# ── Departments page ────────────────────────────────────────────


@router.get("/departments", response_class=HTMLResponse)
def departments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request, name="departments_list.html", context={"user": user}
    )


# ── Districts page ──────────────────────────────────────────────


@router.get("/districts", response_class=HTMLResponse)
def districts_page(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request, name="districts_list.html", context={"user": user}
    )


# ── Audit Log page ─────────────────────────────────────────────


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # AuditReport1.md #8: mirrors the API's role/department scoping in
    # routers/audit.py::get_global_audit_log - a "viewer" (read-only, no
    # department per Project_Context.md §3) shouldn't land on a page
    # showing the full cross-department change history either. Redirect
    # to the home page rather than rendering a table this role isn't
    # meant to see, same idea as camera_new_form/camera_edit_form
    # redirecting non-dept_admins away from forms they can't use.
    if user.role not in ("dept_admin", "operator"):
        return RedirectResponse(url="/", status_code=302)

    from shared.db.models import Camera as CameraModel
    from shared.db.models import StatusHistory as StatusHistoryModel

    query = db.query(StatusHistoryModel).options(joinedload(StatusHistoryModel.camera))
    if user.department_id:
        query = query.filter(
            StatusHistoryModel.camera.has(CameraModel.department_id == user.department_id)
        )
    rows = query.order_by(StatusHistoryModel.changed_at.desc()).limit(200).all()

    user_ids = {r.changed_by for r in rows if r.changed_by is not None}
    users_by_id = {}
    if user_ids:
        users = db.query(UserModel).filter(UserModel.id.in_(user_ids)).all()
        users_by_id = {u.id: u.username for u in users}

    audit_rows = []
    for r in rows:
        camera_name = r.camera.name if r.camera else "Unknown Camera"
        changed_by_user = (
            users_by_id.get(r.changed_by, "System / Direct DB")
            if r.changed_by
            else "System / Direct DB"
        )
        audit_rows.append(
            {
                "changed_at": (
                    r.changed_at.strftime("%Y-%m-%d %H:%M:%S")
                    if r.changed_at
                    else "—"
                ),
                "camera_name": camera_name,
                "changed_field": r.changed_field,
                "old_value": r.old_value,
                "new_value": r.new_value,
                "changed_by_user": changed_by_user,
            }
        )

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={"user": user, "audit_rows": audit_rows},
    )


# ── Gap Analysis page ───────────────────────────────────────────


@router.get("/gap-analysis", response_class=HTMLResponse)
def gap_analysis_page(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request, name="gap_analysis.html", context={"user": user}
    )


# ── Phase 9 — Model 2 placeholders ─────────────────────────────


@router.get("/detections", response_class=HTMLResponse)
@router.get("/detection", response_class=HTMLResponse)
def detections_page(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    """Phase 5 — Live AI Vehicle Detection Dashboard."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Resolve Camera 04 and Camera 22 UUIDs for the JS to use with /api/v1/grid/streams
    cam04 = db.query(CameraModel).filter(CameraModel.source_grid_id == "4").first()
    cam22 = db.query(CameraModel).filter(CameraModel.source_grid_id == "22").first()

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="detection.html",
        context={
            "user": user,
            "cam04_id": str(cam04.id) if cam04 else None,
            "cam22_id": str(cam22.id) if cam22 else None,
        },
    )


@router.get("/recorded-detection", response_class=HTMLResponse)
def recorded_detection_page(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    """Phase 9 — Pre-Recorded Video AI Detection Dashboard."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="recorded_detection.html",
        context={
            "user": user,
        },
    )



# ── Watchlist page (Model 2) ───────────────────────────────────


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    departments = db.query(DeptModel).order_by(DeptModel.name).all()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="watchlist.html",
        context={
            "user": user,
            "departments": departments,
        },
    )


@router.get("/watchlist/persons", response_class=HTMLResponse)
def persons_watchlist_page(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    """Person Watchlist & Facial Biometric Registry Page."""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="persons_watchlist.html",
        context={
            "user": user,
        },
    )


@router.get("/alerts", response_class=HTMLResponse)
def alerts_placeholder(
    request: Request,
    user: Optional[UserModel] = Depends(get_optional_current_user),
):
    # Every other page in this router redirects an anonymous visitor to
    # /login - this one didn't (AuditReport1.md finding 20 / 4.2), so an
    # unauthenticated visitor could reach it directly even though it's
    # gated in the nav for logged-in users only. Nothing sensitive is
    # rendered here (it's a static "not built yet" placeholder), but the
    # inconsistency was worth closing to match every sibling page.
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="placeholder.html",
        context={
            "user": user,
            "page_title": "Alerts",
            "description": "Model 2 — not built yet, see docs/API_Contract.md §2",
        },
    )
