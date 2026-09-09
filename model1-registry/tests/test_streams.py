"""
Auth coverage for streams.py — previously had zero test references at all
(see AuditReport1.md finding 1.1). These only assert the anonymous-401
boundary; they intentionally don't exercise the actual RTSP/OpenCV
capture path (which needs a real camera grid to connect to) since the
auth dependency now rejects the request before any of that code runs.
"""

import uuid


def test_grid_frame_requires_auth(anon_client):
    resp = anon_client.get("/api/v1/cameras/grid/cam01/frame")
    assert resp.status_code == 401


def test_grid_live_requires_auth(anon_client):
    resp = anon_client.get("/api/v1/cameras/grid/cam01/live")
    assert resp.status_code == 401


def test_camera_uuid_live_requires_auth(anon_client):
    resp = anon_client.get(f"/api/v1/cameras/{uuid.uuid4()}/live")
    assert resp.status_code == 401


def test_stream_catalogue_requires_auth(anon_client):
    resp = anon_client.get("/api/v1/streams/catalogue")
    assert resp.status_code == 401


def test_grid_frame_accessible_when_authenticated(admin_home_client):
    resp = admin_home_client.get("/api/v1/cameras/grid/cam01/frame")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_stream_catalogue_accessible_when_authenticated(admin_home_client):
    resp = admin_home_client.get("/api/v1/streams/catalogue")
    assert resp.status_code == 200
    body = resp.json()
    assert "cameras" in body
