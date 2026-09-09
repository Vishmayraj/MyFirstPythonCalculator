"""
Shared fixtures for model2_analytics/tests/.

model2's routers get mounted at runtime into *model1-registry's*
FastAPI app object (see model1-registry/app/main.py's dynamic
importlib loader) -- there is no separate model2 app to spin up.
That means the DB-backed fixtures model1-registry/tests/conftest.py
already built (real Postgres+PostGIS `sentinel_test`, one transaction
+ SAVEPOINT per test, a TestClient per seeded role) are exactly the
right fixtures for testing model2's endpoints too, not a different set
that would have to be kept behaviorally identical to them.

Rather than forking/duplicating that ~300-line fixture file (and
risking the two drifting out of sync the way AuditReport2.md finding 5
warned about for the old ingestion-package split), this loads it by
file path and re-exports its names into this module's namespace, so
pytest sees the same fixtures here as it does under
model1-registry/tests/. Loaded under a distinct module name (not
"conftest") deliberately -- this file is *also* named conftest.py, so
a plain `from conftest import *` would resolve to itself (already
mid-import in sys.modules) instead of model1-registry's copy.

Requires the same local setup as model1-registry/tests: a reachable
Postgres server, `sentinel`/`sentinel_test` bootstrapped per
model1-registry/README.md's Testing section (or `PSQL_PATH` set).

sys.path -- simpler than it used to be
---------------------------------------
Three things need to be importable:
  1. `app` -> model1-registry/app/ (needs MODEL1_ROOT on sys.path).
     grid.py and recorded.py both do plain `from app.auth... import
     ...`.
  2. `pipeline` -> model2_analytics/pipeline/, or recorded.py's
     `from pipeline.video_worker import ...` fails at import time --
     main.py's dynamic loader swallows that silently (see its own
     comments) and just never mounts recorded.py's router, so every
     recorded.py endpoint 404s instead of enforcing auth (needs
     MODEL2_ROOT on sys.path).
  3. `shared` -> repo root's shared/ (needs REPO_ROOT on sys.path).

AuditReport2.md finding 5 used to make this fragile: model2_analytics/
was *also* the name of a second, separate `app` package back when the
real ingestion implementation lived in a hyphenated model2-analytics/
directory with a thin underscored shim alongside it, so MODEL1_ROOT
had to be inserted *after* MODEL2_ROOT, unconditionally, to reliably
win the `app` name. Now that finding 5's fix has consolidated
everything into one model2_analytics/ package (no top-level `app` of
its own - it's model2_analytics.app, a dotted subpackage), there's
no more collision and no ordering to get right: each name has exactly
one place it can resolve to, so plain unconditional inserts are
enough regardless of what order pytest happens to run these three
lines in relative to model1-registry's own conftest.py.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL1_ROOT = REPO_ROOT / "model1-registry"
MODEL2_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(MODEL2_ROOT / "app"))  # for `ingestion` (see supervisor.py)
sys.path.insert(0, str(MODEL2_ROOT))          # for `pipeline`
sys.path.insert(0, str(MODEL1_ROOT))          # for `app`
sys.path.insert(0, str(REPO_ROOT))            # for `shared`

_MODEL1_CONFTEST_PATH = MODEL1_ROOT / "tests" / "conftest.py"

_spec = importlib.util.spec_from_file_location("model1_registry_conftest", _MODEL1_CONFTEST_PATH)
_model1_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_model1_conftest)

# Re-export everything public (pytest fixtures, the _login/unique_camera_name
# helpers, SEED_PASSWORD, etc.) into this module's namespace so pytest picks
# up the fixtures when collecting tests under model2_analytics/tests/.
for _name in dir(_model1_conftest):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_model1_conftest, _name)
del _name, _spec
