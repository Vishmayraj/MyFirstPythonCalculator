def test_login_success_sets_httponly_cookie(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_home", "password": "password123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["user"]["username"] == "admin_home"
    assert body["user"]["role"] == "dept_admin"
    assert body["user"]["department_id"] is not None

    cookie = resp.cookies.get("access_token")
    assert cookie is not None
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header


def test_login_cookie_secure_flag_matches_debug_setting(client):
    """AuditReport1.md finding 2.1: the cookie must not claim `Secure` while
    running with DEBUG=true (the local-dev/test default, no TLS present) -
    a browser would otherwise be fine, but this would be a lie about the
    cookie's own guarantees. settings.DEBUG=False (docker-compose's
    default, see infra/Caddyfile for the TLS termination that makes
    `Secure` meaningful there) is exercised in test_config.py's subprocess
    tests instead, since Settings() here is fixed for the whole process.
    """
    from app.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_home", "password": "password123"},
    )
    assert resp.status_code == 200
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert settings.DEBUG is True, "test suite is expected to run with DEBUG=true"
    assert "Secure" not in set_cookie_header


def test_login_wrong_password_rejected(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_home", "password": "not-the-password"},
    )
    assert resp.status_code == 401


def test_login_unknown_username_rejected(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "someone_who_does_not_exist", "password": "password123"},
    )
    assert resp.status_code == 401


def test_protected_endpoint_requires_auth(anon_client):
    # create_camera requires dept_admin auth
    resp = anon_client.post("/api/v1/cameras", json={"name": "Should Fail"})
    assert resp.status_code == 401


def test_logout_clears_session(admin_home_client):
    # Confirm authenticated first
    assert admin_home_client.get("/api/v1/cameras").status_code == 200

    resp = admin_home_client.post("/api/v1/auth/logout")
    assert resp.status_code in (200, 204)

    # Map page should now redirect to /login since user is anonymous again.
    # (TestClient follows redirects by default; check final URL.)
    page = admin_home_client.get("/", follow_redirects=False)
    assert page.status_code in (302, 303, 307)
    assert "/login" in page.headers.get("location", "")


def test_login_lockout_after_repeated_failures(client):
    """AuditReport1.md finding 2.2: repeated wrong-password attempts must
    eventually get throttled instead of allowed forever."""
    from app.config import settings

    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin_home", "password": "not-the-password"},
        )
        assert resp.status_code == 401

    # One more attempt - even with the *correct* password now - must be
    # throttled, since the lockout blocks the (ip, username) pair itself,
    # not just further wrong guesses.
    locked_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_home", "password": "password123"},
    )
    assert locked_resp.status_code == 429
    assert "Retry-After" in locked_resp.headers


def test_login_lockout_is_scoped_to_username_not_shared_across_accounts(client):
    """Locking out one account from an IP must not collateral-lock a
    different account from that same IP."""
    from app.config import settings

    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin_home", "password": "not-the-password"},
        )

    # admin_rto is a different account; same TestClient (same "IP"), but
    # a different rate-limit key entirely.
    other_resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin_rto", "password": "password123"},
    )
    assert other_resp.status_code == 200


def test_operator_and_viewer_can_login_but_are_not_dept_admin(operator_client, viewer_client):
    assert operator_client.get("/api/v1/cameras").status_code == 200
    assert viewer_client.get("/api/v1/cameras").status_code == 200

    # Neither role may create a camera (require_role("dept_admin"))
    assert operator_client.post("/api/v1/cameras", json={"name": "X"}).status_code == 403
    assert viewer_client.post("/api/v1/cameras", json={"name": "X"}).status_code == 403
