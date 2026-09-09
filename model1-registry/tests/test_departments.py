"""
Tests for departments.py — previously had no test file at all
(AuditReport1.md Section 6: "No test_departments.py ... exists at
all - ... currently 0% coverage, not just gaps in it.").
"""


def test_list_departments_requires_auth(anon_client):
    resp = anon_client.get("/api/v1/departments")
    assert resp.status_code == 401


def test_list_departments_returns_seeded_departments(admin_home_client):
    resp = admin_home_client.get("/api/v1/departments")
    assert resp.status_code == 200
    depts = resp.json()
    assert len(depts) > 0
    names = {d["name"] for d in depts}
    assert any("Home Department" in n for n in names)
    assert any("RTO" in n for n in names)


def test_department_camera_count_matches_active_cameras(admin_home_client):
    depts = {d["name"]: d for d in admin_home_client.get("/api/v1/departments").json()}
    cameras = admin_home_client.get("/api/v1/cameras").json()

    expected_counts = {}
    for cam in cameras:
        if cam["department_name"]:
            expected_counts[cam["department_name"]] = (
                expected_counts.get(cam["department_name"], 0) + 1
            )

    for dept_name, expected in expected_counts.items():
        assert depts[dept_name]["camera_count"] == expected
