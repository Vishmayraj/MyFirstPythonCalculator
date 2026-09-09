"""
ANPR Alert Pipeline Demo
========================
Demonstrates the full ANPR pipeline: OCR -> Watchlist Check -> Alert Generation
This demo uses an in-memory watchlist (no database required).
"""

import sys
sys.path.insert(0, 'model2-analytics')

from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timezone
import uuid


# Simulated watchlist entry
@dataclass
class WatchlistEntry:
    plate_number: str
    category: str  # stolen, wanted, blacklisted
    description: str
    severity: str


# Simulated alert
@dataclass
class Alert:
    alert_id: str
    plate: str
    category: str
    description: str
    severity: str
    camera_name: str
    timestamp: str
    vehicle_type: Optional[str] = None
    confidence: Optional[float] = None


# Simulated watchlist (in production, this comes from the database)
WATCHLIST = [
    WatchlistEntry("MH12AB1234", "stolen", "Stolen vehicle reported in Mumbai", "high"),
    WatchlistEntry("DL4CA1234", "wanted", "Wanted in connection with crime", "critical"),
    WatchlistEntry("KA01MG1234", "blacklisted", "Multiple traffic violations", "medium"),
    WatchlistEntry("TN01BC1234", "stolen", "Stolen from Chennai parking", "high"),
    WatchlistEntry("WB42AX7446", "wanted", "Suspected in robbery case", "critical"),
    WatchlistEntry("RJ11GB1829", "blacklisted", "Illegal goods transport", "medium"),
    WatchlistEntry("KL07BX7197", "stolen", "Stolen from Kochi", "high"),
    WatchlistEntry("UP84AE9889", "wanted", "Wanted for hit-and-run", "critical"),
]


def normalize_plate(text: str) -> str:
    """Normalize plate text for comparison."""
    return "".join(c for c in text.upper() if c.isalnum())


def check_watchlist(plate: str) -> Optional[WatchlistEntry]:
    """Check if a plate is in the watchlist."""
    normalized = normalize_plate(plate)
    for entry in WATCHLIST:
        if normalize_plate(entry.plate_number) == normalized:
            return entry
    return None


def create_alert(entry: WatchlistEntry, camera_name: str, 
                 vehicle_type: str = None, confidence: float = None) -> Alert:
    """Create an alert for a watchlist match."""
    return Alert(
        alert_id=str(uuid.uuid4()),
        plate=entry.plate_number,
        category=entry.category,
        description=entry.description,
        severity=entry.severity,
        camera_name=camera_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        vehicle_type=vehicle_type,
        confidence=confidence,
    )


def process_plate_for_alerts(plate_text: str, camera_name: str = "Camera-01",
                              vehicle_type: str = None, 
                              confidence: float = None) -> Optional[Alert]:
    """
    Process a plate through the alert pipeline.
    
    Args:
        plate_text: OCR'd plate text
        camera_name: Name of the camera
        vehicle_type: Type of vehicle
        confidence: OCR confidence
        
    Returns:
        Alert if plate is watchlisted, None otherwise
    """
    plate = normalize_plate(plate_text)
    
    if len(plate) < 4:
        print(f"  [SKIP] Plate too short: {plate_text}")
        return None
    
    # Check watchlist
    match = check_watchlist(plate)
    
    if match:
        alert = create_alert(match, camera_name, vehicle_type, confidence)
        print(f"  [ALERT] Plate {plate} is WATCHLISTED!")
        print(f"          Category: {match.category}")
        print(f"          Severity: {match.severity}")
        print(f"          Description: {match.description}")
        print(f"          Camera: {camera_name}")
        return alert
    else:
        print(f"  [OK] Plate {plate} is clean")
        return None


def demo_with_ocr_results():
    """Demo using the actual OCR results from our test."""
    print("=" * 70)
    print("ANPR ALERT PIPELINE DEMO")
    print("=" * 70)
    print()
    
    # These are the plates from our test dataset
    test_plates = [
        ("KL35H5834", "Bus", 0.95),
        ("KL34A465", "Bus", 0.99),
        ("KL35F4337", "Bus", 0.95),
        ("UP84AE9889", "Car", 0.99),  # WATCHLISTED!
        ("KL498262", "Bus", 0.98),
        ("WB42AX7446", "Car", 0.92),  # WATCHLISTED!
        ("MP077524", "Truck", 1.00),
        ("MP04PA0434", "Car", 0.98),
        ("RJ11GB1829", "Motorcycle", 0.89),  # WATCHLISTED!
        ("KL41L7001", "Car", 0.94),
        ("TN58D5353", "Car", 0.99),
        ("KL07BX7197", "Truck", 0.98),  # WATCHLISTED!
        ("UP84AE6664", "Car", 0.99),
        ("KL10AG7249", "Car", 0.99),
        ("TN58AP5280", "Bus", 0.99),
        ("DL3CD1210", "Car", 0.98),
        ("RJ11GB8850", "Car", 0.94),
        ("MP13GA9462", "Tempo", 0.96),
        ("KA09C2763", "Bus", 0.99),
        ("MH18AA1002", "Truck", 0.96),
        ("KA01AJ7533", "Car", 0.99),
    ]
    
    alerts = []
    print(f"Processing {len(test_plates)} plates through alert pipeline...")
    print()
    
    for plate_text, vehicle_type, confidence in test_plates:
        alert = process_plate_for_alerts(
            plate_text, 
            camera_name="CCTV-Cam04",
            vehicle_type=vehicle_type,
            confidence=confidence
        )
        if alert:
            alerts.append(alert)
    
    print()
    print("=" * 70)
    print(f"DEMO RESULTS: {len(alerts)} alerts generated from {len(test_plates)} plates")
    print("=" * 70)
    
    if alerts:
        print()
        print("ALERTS SUMMARY:")
        print("-" * 70)
        for alert in alerts:
            print(f"  [{alert.severity.upper():8s}] {alert.plate:15s} | {alert.category:12s} | {alert.description[:40]}")
    
    return alerts


if __name__ == "__main__":
    demo_with_ocr_results()
