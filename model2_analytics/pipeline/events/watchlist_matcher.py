"""
Phase 3 — Watchlist Matcher
============================
Checks an OCR'd plate against the vehicles_watchlist table.
A matched (stolen/wanted/blacklisted) plate produces an alert record.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("sentinel.watchlist")
logger.setLevel(logging.INFO)

# Severity mapping per watchlist category
CATEGORY_SEVERITY = {
    "stolen":     "high",
    "wanted":     "critical",
    "blacklisted": "medium",
}


@dataclass
class WatchlistMatch:
    watchlist_id:  uuid.UUID
    category:      str       # stolen / wanted / blacklisted
    description:   Optional[str]
    severity:      str


def normalize_plate(text: str) -> str:
    """Uppercase, strip spaces/hyphens/dots — matches DB convention."""
    if not text:
        return ""
    return "".join(c for c in text.upper() if c.isalnum())


class WatchlistMatcher:
    """
    Pure DB lookup (no model loading). Thread-safe — uses a fresh
    session (or caller's session) per check.
    """

    def __init__(self):
        pass

    def check_plate(
        self,
        db: Session,
        plate_text: str,
    ) -> Optional[WatchlistMatch]:
        """Look up plate in active watchlist. Returns match or None."""
        plate = normalize_plate(plate_text)
        if len(plate) < 4:
            return None

        try:
            row = db.execute(
                text("""
                    SELECT id, category, description
                    FROM vehicles_watchlist
                    WHERE plate_number = :plate
                      AND status = 'active'
                    LIMIT 1
                """),
                {"plate": plate},
            ).mappings().first()
        except Exception as e:
            logger.warning(f"Watchlist query failed: {e}")
            return None

        if not row:
            return None

        category = row["category"]
        return WatchlistMatch(
            watchlist_id = row["id"],
            category     = category,
            description  = row.get("description"),
            severity     = CATEGORY_SEVERITY.get(category, "medium"),
        )