"""
Shared pytest fixtures.

Design:
  * Tests run against a REAL Postgres + PostGIS database (``sentinel_test``),
    not sqlite/mocks — this app leans on PostGIS geography functions
    (ST_Buffer, ST_Union, ST_Difference, ST_Area on ::geography) and
    Postgres triggers (status_history audit log, updated_at stamping)
    that have no sqlite equivalent. Testing against anything else would
    not actually exercise the code paths that matter most here.
  * ``shared/db/schema.sql`` + ``triggers.sql`` + ``seed.sql`` are applied
    once per test session (session-scoped), exactly as docker-compose
    does it in production.
  * Each individual test runs inside an outer transaction + SAVEPOINT
    that is rolled back on teardown, so tests can freely create/update/
    delete rows (including calling the real API, which calls db.commit())
    without leaking state into other tests or requiring a full reseed
    per test.
"""

import functools
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# ── Make `app` and `shared` importable ─────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL1_ROOT = Path(__file__).resolve().parents[1]
for p in (str(MODEL1_ROOT), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Must be set before `app.main` is imported anywhere (the `client` fixture
# below does `from app.main import app` lazily, but other fixtures/tests
# may import it sooner) - see app/main.py's lifespan() for why: without
# this, every TestClient started during the test session kicks off a real
# background poll against the live CCTV grid host, which retries with
# backoff against a real network and writes to the DB outside of test
# transaction isolation. That's the single biggest source of pytest
# appearing to hang/freeze with no visible cause, especially with no
# network access (CI, sandboxes, offline dev).
os.environ.setdefault("DISABLE_CATALOGUE_POLL", "true")

# test_streams.py::test_grid_frame_accessible_when_authenticated hits a real
# endpoint (app/routers/streams.py) that genuinely tries to open the live
# grid's RTSP feed via OpenCV/FFmpeg, fails (no real camera/credentials in a
# test environment), and falls back to a placeholder JPEG - this is the app
# working as designed, not a bug, and the test correctly asserts the 200 +
# placeholder response. FFmpeg logs that failed connection attempt (e.g.
# "method DESCRIBE failed: 401 Unauthorized") straight to the process's
# stderr in C, bypassing Python's logging/warnings entirely, so it shows up
# in pytest output looking like an error even though nothing failed. This
# only silences that one noisy line for the test run; it does not change
# what the endpoint does or suppress anything in production.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")  # AV_LOG_QUIET

TEST_DB_URL = "postgresql://sentinel:sentinel_dev@127.0.0.1:5432/sentinel_test"
TEST_DB_NAME = "sentinel_test"
ADMIN_DB_URL = "postgresql://sentinel:sentinel_dev@127.0.0.1:5432/postgres"

SEED_PASSWORD = "password123"  # matches README's documented demo accounts


@functools.lru_cache(maxsize=1)
def _resolve_psql() -> str:
    """Locate the psql executable, resolved once per test session.

    Plain ``"psql"`` (the previous, literal invocation) only works if it's
    already on PATH - true out of the box on most Linux/macOS setups and
    CI images, but a common Windows gap: the official PostgreSQL installer
    only prepends its bin/ directory to PATH for *new* shells/terminals
    opened after install (an already-open terminal or IDE keeps the old
    PATH), and some install methods (zip/portable installs, some package
    managers) never add it to PATH at all. When that happens,
    ``subprocess.run(["psql", ...])`` fails with the distinctly
    unhelpful ``FileNotFoundError: [WinError 2]`` deep inside a pytest
    fixture traceback - nothing about that error says "psql isn't on
    PATH" to whoever hits it, and no PATH-copying fix (see _psql_env
    below, which already fixed a *different*, earlier bug) can paper
    over psql genuinely not being resolvable at all.

    Resolution order:
      1. ``PSQL_PATH`` env var, if set - an explicit escape hatch so
         nobody has to fight PATH at all; just point it at the real
         psql/psql.exe.
      2. ``shutil.which("psql")`` - respects whatever PATH the test
         process actually has, cross-platform, and is what most
         environments (Linux, macOS, a properly-configured Windows PATH)
         hit.
      3. Common Windows install locations for the official installer
         (``Program Files\\PostgreSQL\\<version>\\bin``), highest version
         first, since that's the default install path and installer
         PATH updates don't apply retroactively to a shell that was
         already open when Postgres was installed.
      4. Otherwise, raise a clear, actionable RuntimeError instead of
         letting a bare WinError 2 surface as the failure.
    """
    override = os.environ.get("PSQL_PATH")
    if override:
        if not Path(override).is_file():
            raise RuntimeError(
                f"PSQL_PATH is set to {override!r}, but no file exists there. "
                "Point PSQL_PATH at the full path to your psql executable "
                "(psql.exe on Windows)."
            )
        return override

    found = shutil.which("psql")
    if found:
        return found

    if sys.platform == "win32":
        for base in (
            r"C:\Program Files\PostgreSQL",
            r"C:\Program Files (x86)\PostgreSQL",
        ):
            candidates = sorted(Path(base).glob("*/bin/psql.exe"), reverse=True)
            if candidates:
                return str(candidates[0])

    raise RuntimeError(
        "Could not find the 'psql' executable. The test suite shells out to "
        "psql to build the sentinel_test database from shared/db/schema.sql "
        "(see model1-registry/README.md's Testing section). Either add "
        "PostgreSQL's bin/ directory to your PATH (on Windows this is "
        "usually 'C:\\Program Files\\PostgreSQL\\<version>\\bin' - open a "
        "new terminal after installing, since PATH changes don't apply to "
        "terminals that were already open) or set the PSQL_PATH environment "
        "variable to psql's full path (e.g. psql.exe's location) and rerun."
    )


def _psql_env() -> dict:
    """Inherit the caller's real environment and only add PGPASSWORD.

    Previously this replaced the whole environment with a hardcoded
    ``PATH=/usr/bin:/bin``, which only happens to work on Linux (where psql
    lives under /usr/bin). On Windows there is no /usr/bin, so psql.exe
    (and the DLL search path python itself needs) can't be resolved at
    all, which surfaces as ``FileNotFoundError: [WinError 2]`` for every
    test that touches the database. Extending os.environ instead makes
    this work wherever ``psql`` is already on PATH, on any OS.
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = "sentinel_dev"
    return env


# Every subprocess call below gets a hard ceiling. Without one, a psql
# invocation that hangs (e.g. waiting on a password prompt because
# PGPASSWORD didn't take, or blocked on a lock) looks to whoever's
# running the suite exactly like pytest "freezing" with no explanation -
# they just see it never return. A blunt timeout turns that into a
# normal, readable test failure instead.
_PSQL_TIMEOUT_SECONDS = 30


def _run_psql_command(database: str, sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_resolve_psql(), "-h", "127.0.0.1", "-U", "sentinel", "-d", database,
         "-c", sql],
        env=_psql_env(),
        capture_output=True, text=True,
        timeout=_PSQL_TIMEOUT_SECONDS,
    )


def _run_psql(database: str, sql_file: Path) -> None:
    result = subprocess.run(
        [
            _resolve_psql(),
            "-h", "127.0.0.1",
            "-U", "sentinel",
            "-d", database,
            "-v", "ON_ERROR_STOP=1",
            "-f", str(sql_file),
        ],
        env=_psql_env(),
        capture_output=True,
        text=True,
        timeout=_PSQL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        hint = ""
        if 'extension "vector" is not available' in result.stderr:
            hint = (
                "\n\nThis specific error means your local Postgres server is "
                "missing the pgvector extension binary (schema.sql got through "
                "postgis and pgcrypto fine, then hit this on the third "
                "extension). Two fixes, easiest first:\n"
                "  1. Skip installing pgvector locally: stop your local Postgres "
                "service, then `cd infra && docker compose up -d db` (its image "
                "already bundles postgis + pgcrypto + pgvector) and re-run "
                "pytest - it connects to 127.0.0.1:5432 either way, so nothing "
                "else changes.\n"
                "  2. Install pgvector for your local Postgres directly - see "
                "https://github.com/pgvector/pgvector#installation "
                "(Windows needs Visual Studio's C++ build tools; Linux/Mac is "
                "usually a one-line package install, e.g. "
                "`postgresql-16-pgvector` on Debian/Ubuntu).\n"
                "See model1-registry/README.md's Testing section for more."
            )
        elif 'extension "postgis" is not available' in result.stderr:
            hint = (
                "\n\nYour local Postgres server is missing the PostGIS "
                "extension binary. Either install it directly (e.g. "
                "`postgresql-16-postgis-3` on Debian/Ubuntu, `postgis` via "
                "Homebrew, or the PostGIS bundle in the Windows installer's "
                "Application Stack Builder), or skip local installs entirely "
                "with `cd infra && docker compose up -d db` (bundles it "
                "already) and re-run pytest against 127.0.0.1:5432."
            )
        raise RuntimeError(
            f"psql failed applying {sql_file.name} to {database}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}{hint}"
        )


def _sentinel_role_reachable() -> bool:
    """Quick, bounded check that the `sentinel` role/server are already
    usable, so the happy path (everything already bootstrapped) never
    pays for a bootstrap-script invocation."""
    try:
        result = subprocess.run(
            [_resolve_psql(), "-h", "127.0.0.1", "-U", "sentinel", "-d", "postgres",
             "-c", "SELECT 1;"],
            env=_psql_env(),
            capture_output=True, text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, RuntimeError):
        return False
    return result.returncode == 0


def _ensure_local_role_and_db() -> None:
    """New-clone convenience: if the `sentinel` role/database don't exist
    yet on a local (non-Docker) Postgres install, create them via
    scripts/bootstrap_local_db.sh instead of making every new contributor
    hunt down the one-time setup step themselves. `docker compose up`
    already gets this for free from the official Postgres image's
    POSTGRES_USER/POSTGRES_DB env vars (see infra/docker-compose.yml +
    Dockerfile.db) - this mirrors that same zero-setup experience for
    people running tests directly on the host.

    Best-effort and non-fatal: bash-only (skipped on Windows, which
    doesn't have the sudo/su pattern the script relies on to become the
    Postgres admin anyway), and any failure here just falls through to
    the normal psql calls below, which raise their own clear error.
    """
    if _sentinel_role_reachable():
        return
    if sys.platform == "win32" or not shutil.which("bash"):
        return
    script = REPO_ROOT / "scripts" / "bootstrap_local_db.sh"
    if not script.is_file():
        return
    try:
        subprocess.run(
            ["bash", str(script)],
            env=_psql_env(),
            capture_output=True, text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pass  # fall through - the psql calls below will surface a clear error


@pytest.fixture(scope="session")
def test_engine():
    """Build a fresh sentinel_test database from the real schema/triggers/seed
    once for the whole test session, and return an engine bound to it."""
    _ensure_local_role_and_db()

    # A previous session's engine can leave an "idle in transaction"
    # connection behind (e.g. the process was killed mid-test, or a
    # fixture teardown didn't run), which makes the DROP DATABASE below
    # fail with "database ... is being accessed by other users" even
    # though nothing is actually using the database anymore. Force those
    # connections closed first so a fresh `pytest` run is never blocked
    # by a stale one.
    terminate = _run_psql_command(
        "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{TEST_DB_NAME}' AND pid <> pg_backend_pid();",
    )
    if terminate.returncode != 0:
        raise RuntimeError(
            f"psql failed terminating stale connections to {TEST_DB_NAME}:\n"
            f"stdout={terminate.stdout}\nstderr={terminate.stderr}"
        )

    drop = _run_psql_command("postgres", f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}";')
    if drop.returncode != 0:
        raise RuntimeError(
            f"psql failed dropping {TEST_DB_NAME}:\n"
            f"stdout={drop.stdout}\nstderr={drop.stderr}"
        )

    create = _run_psql_command("postgres", f'CREATE DATABASE "{TEST_DB_NAME}" OWNER sentinel;')
    if create.returncode != 0:
        raise RuntimeError(
            f"psql failed creating {TEST_DB_NAME}:\n"
            f"stdout={create.stdout}\nstderr={create.stderr}"
        )

    db_dir = REPO_ROOT / "shared" / "db"
    _run_psql(TEST_DB_NAME, db_dir / "schema.sql")
    _run_psql(TEST_DB_NAME, db_dir / "triggers.sql")
    _run_psql(TEST_DB_NAME, db_dir / "seed.sql")

    engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    """One test = one outer transaction + SAVEPOINT, rolled back at the end.

    App code calls ``session.commit()`` (see cameras.py / pages.py). Under a
    plain session that would end the test's transaction early. Instead we
    bind the session to a connection that already has an open outer
    transaction, and re-open a SAVEPOINT every time the inner transaction
    ends (i.e. every commit), so nothing the app does can escape the
    outer rollback.
    """
    connection = test_engine.connect()
    outer_trans = connection.begin()

    TestingSessionLocal = sessionmaker(bind=connection)
    session = TestingSessionLocal()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    outer_trans.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    """A TestClient whose every request uses the isolated db_session."""
    from app.main import app
    from shared.db.session import get_db

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(c: TestClient, username: str, password: str = SEED_PASSWORD) -> TestClient:
    resp = c.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"login as {username} failed: {resp.text}"
    return c


# ── Seeded demo users (see shared/db/seed.sql) ─────────────────────
# admin_home -> dept_admin, Home Department (Police)
# admin_rto  -> dept_admin, Regional Transport Office (RTO)
# operator1  -> operator,   no department
# viewer1    -> viewer,     no department
#
# IMPORTANT: each of these builds its OWN TestClient (own cookie jar) even
# though they all share the same underlying `db_session` transaction. A
# previous version of this fixture set reused pytest's cached `client`
# fixture for every role, which meant "admin_home_client" and
# "admin_rto_client" were literally the same object with the same cookie
# jar — logging in as the second user silently logged the first one out
# for the rest of the test. That produced false-positive RBAC bugs
# (cross-department writes appearing to succeed because the "admin_home"
# client was actually authenticated as admin_rto by the time the test
# body ran). Keep these independent.


def _independent_client(db_session):
    from app.main import app
    from shared.db.session import get_db

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    c = TestClient(app)
    c.__enter__()
    return c


@pytest.fixture()
def admin_home_client(db_session):
    c = _independent_client(db_session)
    yield _login(c, "admin_home")
    c.__exit__(None, None, None)


@pytest.fixture()
def admin_rto_client(db_session):
    c = _independent_client(db_session)
    yield _login(c, "admin_rto")
    c.__exit__(None, None, None)


@pytest.fixture()
def operator_client(db_session):
    c = _independent_client(db_session)
    yield _login(c, "operator1")
    c.__exit__(None, None, None)


@pytest.fixture()
def viewer_client(db_session):
    c = _independent_client(db_session)
    yield _login(c, "viewer1")
    c.__exit__(None, None, None)


@pytest.fixture()
def anon_client(client):
    return client


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    """The login rate limiter (app/auth/rate_limit.py) is a module-level
    singleton shared across the whole test session, same as streams.py's
    _STREAM_READERS. Without resetting it between tests, a handful of
    deliberate wrong-password tests could accumulate toward the same
    lockout threshold real users hit, and start failing unrelated,
    later tests with 429s instead of the auth result they're actually
    testing for."""
    from app.auth.rate_limit import login_rate_limiter

    login_rate_limiter.reset_all()
    yield
    login_rate_limiter.reset_all()


def unique_camera_name(prefix: str = "Test Cam") -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"
