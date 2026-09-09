"""
Unit tests for app.routers.grid.is_safe_url() -- the SSRF guard on the
catalogue-sync endpoint (AuditReport2.md finding 6 / Section 5 note 3).

Pure-function test: no DB, no running app, no pytest fixtures beyond the
module-scoped import below. Exercises the exact attack classes the audit
called out by name (cloud-metadata IP, loopback, .internal hosts) plus
the scheme allowlist and the malformed-URL fallback, so a future
refactor of this function can't silently drop one of them without a
test failing.

Import note: grid.py lives at model2_analytics/app/routers/grid.py -- a
physically different directory from model1-registry/app/routers/, even
though grid.py's own top-level `from app.auth... import ...` /
`from shared... import ...` lines only resolve correctly when
*model1-registry's* `app` package is the one sys.path finds (which
conftest.py's sys.path setup ensures). A plain `from
app.routers.grid import is_safe_url` can't reach it -- that would look
for model1-registry/app/routers/grid.py, which doesn't exist -- so
this loads it by explicit file path via importlib instead, the same
technique model1-registry/app/main.py itself uses to mount model2's
routers. This is unrelated to the old model2-analytics/ +
model2_analytics/ duplicate-`app`-package situation AuditReport2.md
finding 5 fixed (see conftest.py's docstring for that story) -- it's
just that grid.py's directory was never part of model1-registry's own
app.routers package to begin with.

sys.path itself is arranged by this directory's conftest.py, not here
-- conftest.py always runs before any test file in its directory, and
centralizing it there means every test file in this directory sees the
same sys.path rather than each file guessing at its own.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_GRID_PATH = REPO_ROOT / "model2_analytics" / "app" / "routers" / "grid.py"
_spec = importlib.util.spec_from_file_location("model2_tests._grid_under_test", _GRID_PATH)
_grid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_grid)

is_safe_url = _grid.is_safe_url


class TestSafeUrlsAllowed:
    @pytest.mark.parametrize("url", [
        "https://cctv.corp8.cloud/cam01/index.m3u8",
        "http://103.250.160.189:8889/stream/cam01/whep",
        "rtsp://103.250.160.189:8554/stream/cam01",
        "https://example.com/path?query=1",
    ])
    def test_legitimate_grid_urls_pass(self, url):
        assert is_safe_url(url) is True

    def test_none_is_treated_as_safe(self):
        # Matches the function's own contract: an absent/optional URL
        # field isn't itself a finding, only a present-but-dangerous one.
        assert is_safe_url(None) is True

    def test_empty_string_is_treated_as_safe(self):
        assert is_safe_url("") is True


class TestSsrfTargetsBlocked:
    def test_cloud_metadata_ip_blocked(self):
        """169.254.169.254 is the AWS/GCP/Azure instance-metadata IP --
        the classic SSRF pivot to steal cloud credentials."""
        assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "0.0.0.0", "::1"])
    def test_loopback_hosts_blocked(self, host):
        assert is_safe_url(f"http://{host}:8080/") is False

    def test_loopback_with_port_and_path_blocked(self):
        assert is_safe_url("https://127.0.0.1:5432/steal") is False

    @pytest.mark.parametrize("url", [
        "http://backend.internal/admin",
        "https://db.internal:5432/",
        "http://a.b.c.internal/",
    ])
    def test_internal_suffix_hosts_blocked(self, url):
        assert is_safe_url(url) is False


class TestSchemeAndMalformedInput:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/",
        "javascript:alert(1)",
        "gopher://example.com/",
    ])
    def test_disallowed_schemes_blocked(self, url):
        assert is_safe_url(url) is False

    def test_url_with_no_host_blocked(self):
        assert is_safe_url("http:///no-host-here") is False

    def test_garbage_input_fails_closed(self):
        # urlparse() shouldn't actually raise on most garbage strings, but
        # the function wraps everything in try/except and fails closed --
        # confirm that path returns False rather than propagating.
        assert is_safe_url("::::not a url::::") is False
