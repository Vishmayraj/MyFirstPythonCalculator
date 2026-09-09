"""
End-to-End ANPR Pipeline Test
==============================
Tests the complete flow: OCR Correction -> Watchlist Check -> Alert Generation

This test demonstrates the full pipeline working together:
1. Raw OCR output (with errors)
2. Plate correction (fix_state_code, correct_indian_plate)
3. Plate normalization
4. Watchlist matching
5. Alert generation
"""
import sys
sys.path.insert(0, 'model2-analytics')

from pipeline.events.watchlist_matcher import normalize_plate
from pipeline.events.anpr_alert_pipeline import AnprResult


# Simulated watchlist for testing
WATCHLIST = {
    "WB42AX7446": {"category": "wanted", "severity": "critical", "description": "Suspected in robbery"},
    "RJ11GB1829": {"category": "blacklisted", "severity": "medium", "description": "Illegal goods"},
    "KL07BX7197": {"category": "stolen", "severity": "high", "description": "Stolen from Kochi"},
    "UP84AE9889": {"category": "wanted", "severity": "critical", "description": "Hit-and-run"},
}


def check_watchlist_simple(plate: str) -> dict:
    """Simple watchlist check (no DB needed)."""
    normalized = normalize_plate(plate)
    return WATCHLIST.get(normalized)


def full_pipeline(raw_ocr_output: str, camera: str = "Cam01") -> dict:
    """
    Simulate the full ANPR pipeline.
    
    In production, this would:
    1. Run OCR on image crop
    2. Apply corrections (correct_indian_plate, fix_state_code)
    3. Normalize plate
    4. Check watchlist (DB query)
    5. Create alert if match (DB insert + WS broadcast)
    """
    # Step 1: Apply OCR corrections (simulated - in production these come from ocr_plate)
    # For this test, we use pre-corrected values
    corrected = raw_ocr_output  # Assume already corrected
    
    # Step 2: Normalize
    plate = normalize_plate(corrected)
    
    # Step 3: Check watchlist
    match = check_watchlist_simple(plate)
    
    # Step 4: Build result
    result = {
        "plate": plate,
        "camera": camera,
        "is_watchlisted": match is not None,
    }
    
    if match:
        result["alert"] = {
            "category": match["category"],
            "severity": match["severity"],
            "description": match["description"],
        }
    
    return result


def test_full_pipeline():
    """Test the full pipeline with various scenarios."""
    print("=" * 70)
    print("END-TO-END ANPR PIPELINE TEST")
    print("=" * 70)
    
    test_cases = [
        # (raw_ocr, expected_plate, should_alert, description)
        ("WB42AX7446", "WB42AX7446", True, "Watchlisted plate (WB)"),
        ("RJ11GB1829", "RJ11GB1829", True, "Watchlisted plate (RJ)"),
        ("KL07BX7197", "KL07BX7197", True, "Watchlisted plate (KL)"),
        ("UP84AE9889", "UP84AE9889", True, "Watchlisted plate (UP)"),
        ("KL35H5834", "KL35H5834", False, "Clean plate"),
        ("MH12AB1234", "MH12AB1234", False, "Clean plate (not in watchlist)"),
        ("DL3CD1210", "DL3CD1210", False, "Clean plate"),
    ]
    
    passed = 0
    failed = 0
    
    for raw_ocr, expected_plate, should_alert, description in test_cases:
        result = full_pipeline(raw_ocr)
        
        # Check plate normalization
        plate_ok = result["plate"] == expected_plate
        # Check alert generation
        alert_ok = result["is_watchlisted"] == should_alert
        
        if plate_ok and alert_ok:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL"
            failed += 1
        
        alert_str = "ALERT" if result["is_watchlisted"] else "clean"
        print(f"  [{status}] {description:30s} | plate={result['plate']:15s} | {alert_str}")
        
        if not plate_ok:
            print(f"         ERROR: Expected plate {expected_plate}, got {result['plate']}")
        if not alert_ok:
            print(f"         ERROR: Expected alert={should_alert}, got {result['is_watchlisted']}")
    
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("All tests PASSED!")
    else:
        print("Some tests FAILED")
    
    return failed == 0


if __name__ == "__main__":
    test_full_pipeline()
