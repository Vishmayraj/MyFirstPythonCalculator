"""Event/alert management subpackage — watchlist matching + alert creation."""
from pipeline.events.watchlist_matcher import WatchlistMatcher, WatchlistMatch, normalize_plate
from pipeline.events.alert_service import AlertService

__all__ = ["WatchlistMatcher", "WatchlistMatch", "normalize_plate", "AlertService"]
