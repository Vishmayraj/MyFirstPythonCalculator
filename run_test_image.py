"""
Run the full ANPR pipeline on a test image.
Tests: Vehicle detection -> Plate detection -> OCR -> Correction -> Alert check
"""
import sys, os, json, time, re, warnings, cv2, numpy as np
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")
sys.path.insert(0, 'model2-analytics')

from pipeline.detection.vehicle_detector import VehicleDetector
from pipeline.plate.plate_detector import PlateDetector
from pipeline.ocr.onnx_ocr_engine import OnnxOCREngine, extract_indian_plate, INDIAN_PLATE_RE
from pipeline.events.watchlist_matcher import normalize_plate

# Import correction functions from final_anpr_test_v2.py
with open('final_anpr_test_v2.py', encoding='utf-8') as f:
    src = f.read()
import_start = src.find('import sys')
main_loop_start = src.find('# ==== MAIN LOOP')
func_block = src[import_start:main_loop_start]
ns = {'re': re, 'INDIAN_PLATE_RE': INDIAN_PLATE_RE}
exec(func_block, ns)
correct_indian_plate = ns['correct_indian_plate']
fix_state_code = ns['fix_state_code']
fix_reordered_plate = ns['fix_reordered_plate']

# Config
TEST_IMAGE = "Test Input/IMG-20260718-WA0004.jpg"
PLATE_MODEL = "model2-analytics/pipeline/plate/indian_models/license_finetuned.pt"
EXPECTED_PLATE = "GJ01AB1234"

# Simulated watchlist
WATCHLIST = {
    "WB42AX7446": {"category": "wanted", "severity": "critical", "description": "Suspected in robbery"},
    "RJ11GB1829": {"category": "blacklisted", "severity": "medium", "description": "Illegal goods"},
    "KL07BX7197": {"category": "stolen", "severity": "high", "description": "Stolen from Kochi"},
    "UP84AE9889": {"category": "wanted", "severity": "critical", "description": "Hit-and-run"},
    "GJ01AB1234": {"category": "stolen", "severity": "high", "description": "White Swift stolen in Ahmedabad district"},
}
def ocr_plate(crop):
    """OCR a plate crop with two-line reordering + position-aware correction."""
    processed = ocr_engine._preprocess(crop)
    result = ocr_engine._model.ocr(processed)
    if not result or not result[0]:
        return None
    lines = []
    for line in result[0]:
        if len(line) < 2:
            continue
        text = line[1][0]
        conf = float(line[1][1])
        box = line[0]
        cy = sum(p[1] for p in box) / 4
        cx = sum(p[0] for p in box) / 4
        lines.append((text, conf, cy, cx))
    if not lines:
        return None
    lines_sorted = sorted(lines, key=lambda l: l[2])
    rows = []
    if len(lines_sorted) <= 1:
        rows = [lines_sorted]
    else:
        gaps = [lines_sorted[i+1][2] - lines_sorted[i][2] for i in range(len(lines_sorted)-1)]
        median_gap = sorted(gaps)[len(gaps)//2] if gaps else 0
        threshold = median_gap * 1.2 if median_gap > 0 else 60
        current_row = [lines_sorted[0]]
        for i in range(1, len(lines_sorted)):
            if lines_sorted[i][2] - current_row[-1][2] < threshold:
                current_row.append(lines_sorted[i])
            else:
                rows.append(current_row)
                current_row = [lines_sorted[i]]
        rows.append(current_row)
    for row in rows:
        row.sort(key=lambda l: l[3])
    text_tb = "".join(l[0] for row in rows for l in row)
    raw_tb = "".join(c for c in text_tb.upper() if c.isalnum())
    clean_tb = extract_indian_plate(raw_tb)
    text_bt = "".join(l[0] for row in reversed(rows) for l in row)
    raw_bt = "".join(c for c in text_bt.upper() if c.isalnum())
    clean_bt = extract_indian_plate(raw_bt)
    candidates = []
    if INDIAN_PLATE_RE.match(clean_tb): candidates.append(clean_tb)
    if INDIAN_PLATE_RE.match(clean_bt): candidates.append(clean_bt)
    for raw in [raw_tb, raw_bt]:
        c = extract_indian_plate(raw)
        if INDIAN_PLATE_RE.match(c):
            candidates.append(c)
    if candidates:
        best = max(candidates, key=len)
    else:
        best = clean_tb if len(clean_tb) >= len(clean_bt) else clean_bt
    best = fix_reordered_plate(best)
    best = correct_indian_plate(best)
    best = fix_state_code(best)
    if len(best) < 5:
        return None
    confs = [l[1] for row in rows for l in row]
    avg_conf = sum(confs) / len(confs)
    return {"text": best, "raw": raw_tb, "confidence": avg_conf}

# ============================================================
# MAIN PIPELINE
# ============================================================

print("=" * 70)
print("FULL ANPR PIPELINE TEST")
print("=" * 70)
print(f"Test image: {TEST_IMAGE}")
print(f"Expected plate: {EXPECTED_PLATE}")
print()

# Load image
frame = cv2.imread(TEST_IMAGE)
if frame is None:
    print(f"ERROR: Could not load image {TEST_IMAGE}")
    sys.exit(1)
H, W = frame.shape[:2]
print(f"Image loaded: {W}x{H}")
print()

# Step 1: Load models
print("[1/5] Loading models...")
t0 = time.time()
vehicle_detector = VehicleDetector(confidence_threshold=0.10, iou_threshold=0.45)
plate_detector = PlateDetector(confidence_threshold=0.20, model_path=PLATE_MODEL)
ocr_engine = OnnxOCREngine(use_gpu=False)
ocr_engine._lazy_load()
print(f"  Models loaded in {time.time()-t0:.1f}s")
print()

# Step 2: Vehicle detection
print("[2/5] Running vehicle detection...")
t0 = time.time()
vehicles = vehicle_detector.detect(frame)
print(f"  Found {len(vehicles)} vehicle(s) in {time.time()-t0:.2f}s")
for v in vehicles:
    print(f"    - {v.class_name} (conf: {v.confidence:.2f}) at {v.bbox}")
print()

# Step 3: Plate detection
print("[3/5] Running plate detection...")
t0 = time.time()
plate_dets = plate_detector.detect(frame)
print(f"  Found {len(plate_dets)} plate(s) in {time.time()-t0:.2f}s")
for i, p in enumerate(plate_dets):
    print(f"    - Plate {i}: conf={p.confidence:.2f} at {p.bbox}")
print()

# Step 4: OCR + Correction
print("[4/5] Running OCR + correction pipeline...")
plate_results = []
for i, p in enumerate(plate_dets):
    px1, py1, px2, py2 = p.bbox
    pad = int(0.06 * max(px2-px1, py2-py1))
    crop = frame[max(0, py1-pad):min(H, py2+pad), max(0, px1-pad):min(W, px2+pad)]
    if crop.size == 0:
        continue
    t0 = time.time()
    ocr = ocr_plate(crop)
    ocr_time = time.time() - t0
    if ocr:
        plate_results.append({"bbox": p.bbox, "det_conf": p.confidence, "text": ocr["text"], "ocr_conf": ocr["confidence"], "raw": ocr["raw"], "ocr_time": ocr_time})
        print(f"  Plate {i}: '{ocr['text']}' (raw: '{ocr['raw']}', conf: {ocr['confidence']:.2f}, time: {ocr_time:.2f}s)")
print()

# Step 5: Alert check
print("[5/5] Checking watchlist...")
alerts = []
for pr in plate_results:
    plate = normalize_plate(pr["text"])
    match = WATCHLIST.get(plate)
    pr["normalized"] = plate
    pr["is_watchlisted"] = match is not None
    if match:
        pr["alert"] = match
        alerts.append(pr)
        print(f"  [ALERT] Plate '{plate}' is WATCHLISTED!")
        print(f"          Category: {match['category']}, Severity: {match['severity']}")
        print(f"          Description: {match['description']}")
    else:
        print(f"  [OK] Plate '{plate}' is clean")
print()

# ============================================================
# FINAL RESULTS
# ============================================================

print()
print("=" * 70)
print("FINAL RESULTS")
print("=" * 70)

if plate_results:
    for pr in plate_results:
        normalized = pr["normalized"]
        is_correct = normalized == EXPECTED_PLATE
        status = "CORRECT" if is_correct else "WRONG"
        print(f"  Plate text:   {pr['text']}")
        print(f"  Normalized:   {normalized}")
        print(f"  Expected:     {EXPECTED_PLATE}")
        print(f"  Match:        {status} {'OK' if is_correct else 'FAIL'}")
        print(f"  Det conf:     {pr['det_conf']:.2f}")
        print(f"  OCR conf:     {pr['ocr_conf']:.2f}")
        print(f"  Watchlisted:  {'YES' if pr['is_watchlisted'] else 'NO'}")
        if pr.get('alert'):
            print(f"  Alert:        {pr['alert']['category']} ({pr['alert']['severity']})")
        print()
else:
    print("  No plates detected!")

print(f"Total plates found: {len(plate_results)}")
print(f"Alerts generated: {len(alerts)}")
print("=" * 70)


