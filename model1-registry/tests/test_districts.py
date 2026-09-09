GUJARAT_DISTRICT_COUNT = 33


def test_list_districts_requires_auth(anon_client):
    resp = anon_client.get("/api/v1/districts")
    assert resp.status_code == 401


def test_districts_endpoint_returns_all_33(admin_home_client):
    """Regression test: shared/db/seed.sql previously only ran UPDATE
    statements against district *names* with nothing ever INSERTed, so this
    endpoint silently returned []. Guard against that regressing again."""
    resp = admin_home_client.get("/api/v1/districts")
    assert resp.status_code == 200
    districts = resp.json()
    assert len(districts) == GUJARAT_DISTRICT_COUNT
    names = {d["name"] for d in districts}
    assert len(names) == GUJARAT_DISTRICT_COUNT  # all unique
    assert "Ahmedabad" in names
    assert "Kutch" in names
    assert "Valsad" in names


def test_every_district_has_a_real_multipolygon_boundary(admin_home_client):
    """Guards against the boundaries silently reverting to the old 5-point
    rectangle placeholders (or null) instead of real district shapes."""
    resp = admin_home_client.get("/api/v1/districts")
    districts = resp.json()
    for d in districts:
        assert d["boundary"] is not None, f"{d['name']} has no boundary"
        assert d["boundary"]["type"] == "MultiPolygon"
        # A real, simplified district outline has far more than 5 points
        # per ring; the old placeholder rectangles had exactly 5 (4 corners
        # + closing point). Sum ring lengths across all polygons.
        total_points = sum(
            len(ring)
            for polygon in d["boundary"]["coordinates"]
            for ring in polygon
        )
        assert total_points > 20, (
            f"{d['name']} boundary looks like a placeholder rectangle "
            f"({total_points} total points)"
        )


def test_district_camera_count_matches_active_cameras(admin_home_client):
    districts = {d["name"]: d for d in admin_home_client.get("/api/v1/districts").json()}
    cameras = admin_home_client.get("/api/v1/cameras").json()

    expected_counts = {}
    for cam in cameras:
        if cam["district_name"]:
            expected_counts[cam["district_name"]] = (
                expected_counts.get(cam["district_name"], 0) + 1
            )

    for district_name, expected in expected_counts.items():
        assert districts[district_name]["camera_count"] == expected


def test_seeded_cameras_are_linked_to_a_district(admin_home_client):
    """Regression test: with the districts table empty at insert time, the
    `(SELECT id FROM districts WHERE name = ...)` subqueries in seed.sql's
    camera INSERTs all silently resolved to NULL."""
    cameras = admin_home_client.get("/api/v1/cameras").json()
    assert cameras
    unlinked = [c["name"] for c in cameras if c["district_id"] is None]
    assert not unlinked, f"cameras with no district_id: {unlinked}"
