"""
Unit tests for shared/schemas/watchlist.py's Indian license-plate
validation & normalization (AuditReport2.md finding 6's "pure-function
candidate" suggestion; README's "Strict Indian license plate
normalization & regex" claim under Validation).

Pure pydantic-model tests: no DB, no app, no network. `shared` has no
dependency on either `app` package, so this imports cleanly without
the model1/model2 `app`-name collision that test_is_safe_url.py has to
work around.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.schemas.watchlist import VehicleWatchlistCreate, VehicleWatchlistUpdate

VALID_DESCRIPTION = "Reported stolen from a public parking lot on MG Road"


def _make(plate: str) -> VehicleWatchlistCreate:
    return VehicleWatchlistCreate(
        plate_number=plate,
        category="stolen",
        description=VALID_DESCRIPTION,
    )


class TestStandardFormatAccepted:
    @pytest.mark.parametrize("plate", [
        "GJ01AB1234",
        "DL3C1234",
        "MH12A1234",
        "GJ011234",
    ])
    def test_already_clean_plates_pass_unchanged(self, plate):
        assert _make(plate).plate_number == plate


class TestBharatSeriesAccepted:
    @pytest.mark.parametrize("plate", ["22BH1234AA", "01BH1234A"])
    def test_bharat_series_passes(self, plate):
        assert _make(plate).plate_number == plate


class TestNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("gj01ab1234", "GJ01AB1234"),          # lowercase -> uppercase
        ("GJ 01 AB 1234", "GJ01AB1234"),        # internal whitespace stripped
        ("GJ-01-AB-1234", "GJ01AB1234"),        # hyphens stripped
        ("  GJ01AB1234  ", "GJ01AB1234"),       # leading/trailing whitespace
        ("gj.01.ab.1234", "GJ01AB1234"),        # dots stripped
        ("22 BH 1234 AA", "22BH1234AA"),        # Bharat series, spaced
    ])
    def test_variants_normalize_to_canonical_form(self, raw, expected):
        assert _make(raw).plate_number == expected


class TestInvalidFormatsRejected:
    @pytest.mark.parametrize("plate", [
        "",
        "   ",
        "1234",                 # digits only
        "ABCDEFGH",             # letters only
        "GJ01AB12",             # only 2 trailing digits, needs 4
        "GJ01ABCDE1234",        # too many letters in the middle group
        "ZZ99ZZ999999",         # not a real plate shape at all
        "99BH1234ABC",          # Bharat series with 3 trailing letters (max is 2)
    ])
    def test_invalid_plates_raise(self, plate):
        with pytest.raises(ValidationError):
            _make(plate)


class TestPartialUpdateSchema:
    """VehicleWatchlistUpdate makes every field optional (PATCH semantics)
    but must apply the exact same validate-and-normalize rule to
    plate_number whenever one is actually supplied."""

    def test_omitted_plate_is_left_as_none(self):
        update = VehicleWatchlistUpdate(status="resolved")
        assert update.plate_number is None

    def test_supplied_plate_is_normalized(self):
        update = VehicleWatchlistUpdate(plate_number="gj 01 ab 1234")
        assert update.plate_number == "GJ01AB1234"

    def test_supplied_invalid_plate_raises(self):
        with pytest.raises(ValidationError):
            VehicleWatchlistUpdate(plate_number="not-a-plate")


class TestDescriptionValidation:
    def test_too_short_description_rejected(self):
        with pytest.raises(ValidationError):
            VehicleWatchlistCreate(
                plate_number="GJ01AB1234",
                category="stolen",
                description="short",
            )

    def test_whitespace_only_description_rejected(self):
        with pytest.raises(ValidationError):
            VehicleWatchlistCreate(
                plate_number="GJ01AB1234",
                category="stolen",
                description="            ",
            )
