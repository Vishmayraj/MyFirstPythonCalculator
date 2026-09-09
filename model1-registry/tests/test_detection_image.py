"""
Auth coverage for the /detection-image route — previously a plain
StaticFiles mount with no auth at all (see AuditReport1.md finding 1.5).
"""

import uuid

import pytest


@pytest.fixture()
def sample_detection_image():
    """Write a real file into the (test) detection-image directory and
    clean it up afterward, so we can assert an authenticated request
    actually gets the file back, not just a 401/404."""
    from app.main import DETECTION_IMG_DIR

    name = f"test-{uuid.uuid4().hex[:8]}.jpg"
    path = DETECTION_IMG_DIR / name
    path.write_bytes(b"not a real jpeg, just test bytes")
    try:
        yield name
    finally:
        path.unlink(missing_ok=True)


def test_detection_image_requires_auth(anon_client, sample_detection_image):
    resp = anon_client.get(f"/detection-image/{sample_detection_image}")
    assert resp.status_code == 401


def test_detection_image_served_when_authenticated(admin_home_client, sample_detection_image):
    resp = admin_home_client.get(f"/detection-image/{sample_detection_image}")
    assert resp.status_code == 200
    assert resp.content == b"not a real jpeg, just test bytes"


def test_detection_image_unknown_file_is_404_when_authenticated(admin_home_client):
    resp = admin_home_client.get("/detection-image/does-not-exist.jpg")
    assert resp.status_code == 404


def test_detection_image_path_traversal_is_404_not_leaked(admin_home_client):
    # Even logged in, escaping DETECTION_IMG_DIR must not be servable.
    resp = admin_home_client.get("/detection-image/../../../../etc/passwd")
    assert resp.status_code in (401, 404)
