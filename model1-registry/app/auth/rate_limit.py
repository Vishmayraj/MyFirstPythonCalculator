"""
Login rate limiting / lockout.

AuditReport1.md finding 2.2: `POST /api/v1/auth/login` had no throttling
of any kind - unlimited password guesses forever, from anyone. Low
urgency for the hackathon demo itself (the demo accounts are meant to be
publicly known), but a real gap the moment this is pointed at non-demo
accounts.

This is deliberately a simple in-memory, single-process limiter - no
Redis or DB table. That's the right amount of complexity for how this
app actually runs today (one `app` container in docker-compose, no
horizontal scaling anywhere in infra/). If that ever changes, this needs
a shared backend (e.g. Redis) instead, since separate processes would
each keep their own counters and the lockout would no longer be
effective across all of them.

Locking is keyed on (client IP, username) rather than username alone, so
one attacker can't use this to remotely lock a specific legitimate user
out of their own account from an arbitrary IP - they'd need to be
attacking from the same IP the real user logs in from.
"""

import threading
import time
from typing import Dict, List


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window_seconds: float, lockout_seconds: float):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        # key -> failure timestamps within the current sliding window
        self._failures: Dict[str, List[float]] = {}
        # key -> unix time the lockout expires
        self._locked_until: Dict[str, float] = {}

    def _prune(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        pruned = [t for t in self._failures.get(key, []) if t > cutoff]
        if pruned:
            self._failures[key] = pruned
        else:
            self._failures.pop(key, None)

    def seconds_until_unlocked(self, key: str) -> float:
        """0.0 if `key` may attempt a login right now, else how many
        seconds remain before it can."""
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return 0.0
            now = time.time()
            if now >= until:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)
                return 0.0
            return until - now

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._prune(key, now)
            self._failures.setdefault(key, []).append(now)
            if len(self._failures[key]) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout_seconds

    def record_success(self, key: str) -> None:
        """A successful login clears any accumulated failure count for
        this key - only *consecutive* failures should ever lock someone
        out, not a lifetime tally."""
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def reset_all(self) -> None:
        """Test-only convenience - production code never calls this.
        Without it, the module-level singleton below (shared across every
        test in the suite, same pattern as streams.py's _STREAM_READERS)
        would leak failure counts between unrelated tests."""
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


def rate_limit_key(client_ip: str, username: str) -> str:
    return f"{client_ip}:{username.strip().lower()}"


def _build_default_limiter() -> LoginRateLimiter:
    from app.config import settings

    return LoginRateLimiter(
        max_attempts=settings.LOGIN_MAX_ATTEMPTS,
        window_seconds=settings.LOGIN_WINDOW_SECONDS,
        lockout_seconds=settings.LOGIN_LOCKOUT_SECONDS,
    )


login_rate_limiter = _build_default_limiter()
