""" FINAL ANPR SYSTEM TEST v2 - Indian Dataset + Fine-tuned YOLO + OnnxOCR(PP-OCRv5) """
import sys, os, json, time, re, warnings, itertools
import cv2, numpy as np
from pathlib import Path
from datetime import datetime
warnings.filterwarnings("ignore")
sys.path.insert(0, 'model2-analytics')

from pipeline.detection.vehicle_detector import VehicleDetector
from pipeline.plate.plate_detector import PlateDetector
from pipeline.ocr.onnx_ocr_engine import OnnxOCREngine, extract_indian_plate, INDIAN_PLATE_RE

IMG_DIR = "model2-analytics/ANPR bhidio/anpr dataset/number_plate_images_ocr/number_plate_images_ocr"
XML_DIR = "model2-analytics/ANPR bhidio/anpr dataset/number_plate_annos_ocr/number_plate_annos_ocr"
PLATE_MODEL = "model2-analytics/pipeline/plate/indian_models/license_finetuned.pt"
OUTPUT_DIR = Path("anpr_indian_test_results")
NUM_IMAGES = 20

for d in ["01_initial", "02_intermediate", "03_final", "04_plate_crops"]:
    (OUTPUT_DIR / d).mkdir(parents=True, exist_ok=True)

print("=" * 100)
print("FINAL ANPR TEST v2 - Indian Dataset + Fine-tuned YOLO + OnnxOCR (PP-OCRv5)")
print("=" * 100)

t0 = time.time()
vehicle_detector = VehicleDetector(confidence_threshold=0.10, iou_threshold=0.45)
plate_detector = PlateDetector(confidence_threshold=0.20, model_path=PLATE_MODEL)
ocr_engine = OnnxOCREngine(use_gpu=False)
ocr_engine._lazy_load()
print(f"Models loaded in {time.time()-t0:.1f}s")

IMAGE_FILES = sorted(Path(IMG_DIR).glob("*.jpg"))[:NUM_IMAGES]


def load_gt(xml_path):
    import xml.etree.ElementTree as ET
    if not Path(xml_path).exists():
        return []
    root = ET.parse(xml_path).getroot()
    plates = []
    for obj in root.findall("object"):
        if obj.find("name").text == "number_plate":
            bb = obj.find("bndbox")
            bbox = (int(float(bb.find("xmin").text)), int(float(bb.find("ymin").text)),
                    int(float(bb.find("xmax").text)), int(float(bb.find("ymax").text)))
            text = ""
            for attr in obj.findall(".//attribute"):
                if attr.find("name").text == "number_plate_text":
                    text = attr.find("value").text.strip()
            plates.append((text, bbox))
    return plates


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
    iw = max(0, ix2-ix1); ih = max(0, iy2-iy1)
    inter = iw*ih
    union = (ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter
    return inter/union if union > 0 else 0.0


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
    # Cluster lines into rows by Y-center distance
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
    # Build text in reading order: top-to-bottom, left-to-right within row
    text_tb = "".join(l[0] for row in rows for l in row)
    raw_tb = "".join(c for c in text_tb.upper() if c.isalnum())
    clean_tb = extract_indian_plate(raw_tb)
    # Strategy 2: bottom-to-top (in case OCR got vertical order wrong)
    text_bt = "".join(l[0] for row in reversed(rows) for l in row)
    raw_bt = "".join(c for c in text_bt.upper() if c.isalnum())
    clean_bt = extract_indian_plate(raw_bt)
    # Strategy 3: try all line permutations (for <= 4 lines)
    all_lines = [l[0] for row in rows for l in row]
    clean_perm = None
    if len(all_lines) <= 4:
        for perm in itertools.permutations(range(len(all_lines))):
            text_p = "".join(all_lines[i] for i in perm)
            raw_p = "".join(c for c in text_p.upper() if c.isalnum())
            clean_p = extract_indian_plate(raw_p)
            if INDIAN_PLATE_RE.match(clean_p):
                clean_perm = clean_p
                break
    # Choose: prefer regex match, then longest
    candidates = []
    if INDIAN_PLATE_RE.match(clean_tb): candidates.append(clean_tb)
    if INDIAN_PLATE_RE.match(clean_bt): candidates.append(clean_bt)
    if clean_perm and INDIAN_PLATE_RE.match(clean_perm): candidates.append(clean_perm)
    for raw in [raw_tb, raw_bt]:
        c = extract_indian_plate(raw)
        if INDIAN_PLATE_RE.match(c):
            candidates.append(c)
    if candidates:
        best = max(candidates, key=len)
    else:
        best = clean_tb if len(clean_tb) >= len(clean_bt) else clean_bt
    best = fix_reordered_plate(best)   # fix two-line vertical reorder (e.g. BX7197KL07 -> KL07BX7197)
    best = correct_indian_plate(best)  # position-aware char confusion fixes
    best = fix_state_code(best)        # state-code validation/repair
    if len(best) < 5:
        return None
    confs = [l[1] for row in rows for l in row]
    avg_conf = sum(confs) / len(confs)
    return {"text": best, "raw": raw_tb, "confidence": avg_conf}


def correct_indian_plate(text):
    """Fix OCR confusions in valid positions. Tries corrections even when
    text doesn't match regex yet (e.g. Z in digit position), and returns
    the first correction that produces a valid plate."""
    # Digit-position confusions (positions 2,3 = district code, positions 5-9 = number)
    # Z at position 2-3 → 4 (district codes); Z at position 5-9 → 2 (number digits)
    digit_confusions_pos23 = {"O":"0","Q":"0","I":"1","Z":"4","B":"8","S":"5","A":"4","G":"6"}
    # Letter-position confusions (positions 4,5 should be letters)
    letter_confusions = {"0":"O","1":"I","2":"Z"}
    
        # FIRST: Fix Z in number part (positions 5-9) — Z can look like 2
    # This must run BEFORE the regex check because Z in number part is always wrong
    # e.g. KA09CZ763 → KA09C2763 (Z at pos 5 is first digit of number)
    for pos in range(5, min(len(text), 10)):
        if text[pos] == "Z":
            candidate = list(text)
            candidate[pos] = "2"
            candidate = "".join(candidate)
            if INDIAN_PLATE_RE.match(candidate):
                return candidate
    
    # FIRST-B: Fix state code (positions 0-1) using fix_state_code
    # e.g. UJ11GB1829 → RJ11GB1829
    if len(text) >= 2:
        state_fixed = fix_state_code(text)
        if state_fixed != text:
            return state_fixed
    
    # SECOND: Try the deterministic mapping (for already-valid plates)
    if INDIAN_PLATE_RE.match(text):
        corrected = list(text)
        for i in [2, 3]:
            if i < len(corrected):
                corrected[i] = digit_confusions_pos23.get(corrected[i], corrected[i])
        # Only apply letter_confusions at position 4 (always a series letter)
        # Position 5 can be letter OR digit (depending on series length)
        if len(corrected) > 4:
            corrected[4] = letter_confusions.get(corrected[4], corrected[4])
        result = "".join(corrected)
        if INDIAN_PLATE_RE.match(result):
            return result
    
    # THIRD: For plates that don't match (e.g. Z in digit slot), try confusion replacements
    # at each position to find one that produces a valid plate
    confusions = {
        2: digit_confusions_pos23, 3: digit_confusions_pos23,
        4: letter_confusions, 5: letter_confusions,
    }
    for pos, conf_map in confusions.items():
        if pos < len(text) and text[pos] in conf_map:
            candidate = list(text)
            candidate[pos] = conf_map[text[pos]]
            candidate = "".join(candidate)
            if INDIAN_PLATE_RE.match(candidate):
                return candidate
    return text


VALID_STATE_CODES = {
    "AP","AR","AS","BR","CG","DL","GA","GJ","HR","HP","JH","JK","KA","KL",
    "MH","MP","MN","ML","MZ","NL","OD","PB","RJ","SK","TN","TR","UP","UK",
    "UA","WB","AN","CH","DD","DN","LA","LD","PY",
}

CHAR_CONFUSIONS = {"U":"R","0":"D","O":"D","8":"B","5":"S","2":"Z","1":"L","I":"J","6":"G","4":"A","7":"T","3":"B"}


def fix_reordered_plate(text):
    """Fix two-line plates read in wrong vertical order.
    Pattern: [chars+serial][state+district] -> [state+district][chars+serial]
    e.g. BX7197KL07 -> KL07BX7197, APTN585280 -> TN58AP5280 (via extract first)."""
    if not text or len(text) < 8:
        return text
    m = re.match(r"^([A-Z]{2}[0-9]{2}[A-Z]{1,3}[0-9]{2,4})([A-Z]{2}[0-9]{2})$", text)
    if m:
        candidate = m.group(2) + m.group(1)
        if INDIAN_PLATE_RE.match(candidate):
            return candidate
    m = re.match(r"^([A-Z]{2})([A-Z]{2}[0-9]{2}[0-9]{3,4})$", text)
    if m:
        candidate = m.group(2) + m.group(1)
        if INDIAN_PLATE_RE.match(candidate):
            return candidate
    return text


def fix_state_code(text):
    """If first 2 letters are not a valid Indian state code, try single-char
    confusion repairs on the state code only (e.g. UJ -> RJ, WB ok)."""
    if len(text) < 2 or text[:2] in VALID_STATE_CODES:
        return text
    for i in range(2):
        alt = CHAR_CONFUSIONS.get(text[i])
        if alt:
            # Fix: text[1:] not text[1] — need the FULL rest of the string
            candidate = alt + text[1:] if i == 0 else text[0] + alt + text[2:]
            if candidate[:2] in VALID_STATE_CODES and INDIAN_PLATE_RE.match(candidate):
                return candidate
    return text


def fix_dropped_letter(text):
    """Try inserting a letter at each position to fix plates with dropped chars.
    e.g. MP077524 (8 chars) -> MP07L7524 (9 chars, valid Indian plate)"""
    if INDIAN_PLATE_RE.match(text):
        return text
    # Indian plates are 9-10 chars. If we have 8, try inserting a letter.
    if len(text) == 8:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for pos in range(2, 6):  # Try inserting at positions 2-5 (after state code)
            for ch in letters:
                candidate = text[:pos] + ch + text[pos:]
                if INDIAN_PLATE_RE.match(candidate):
                    return candidate
    return text


# ==============================================================================
# MAIN LOOP
# ==============================================================================

VEHICLE_COLORS = {"Car": (0, 255, 0), "Bus": (255, 165, 0), "Truck": (0, 165, 255),
                  "Auto Rickshaw": (255, 0, 255), "Motorcycle": (0, 255, 255),
                  "Mini-Truck": (0, 165, 255), "Bike": (0, 255, 255)}
GREEN = (60, 220, 60)
RED = (50, 50, 230)

metrics = {"images": 0, "gt_plates": 0, "plates_detected": 0, "plates_matched": 0,
           "ocr_exact": 0, "ocr_correct_len": 0, "vehicles_detected": 0,
           "ocr_time": 0.0, "det_time": 0.0, "veh_time": 0.0}
per_image = []
results_json = []

print(f"\nProcessing {len(IMAGE_FILES)} images...")
for idx, img_path in enumerate(IMAGE_FILES):
    xml_path = Path(XML_DIR) / (img_path.stem + ".xml")
    gt_plates = load_gt(xml_path)
    frame = cv2.imread(str(img_path))
    if frame is None:
        continue
    H, W = frame.shape[:2]
    metrics["images"] += 1
    metrics["gt_plates"] += len(gt_plates)

    # 1. Save initial image (downscaled for storage)
    scale = 1600.0 / W if W > 1600 else 1.0
    init_disp = cv2.resize(frame, (int(W*scale), int(H*scale))) if scale < 1.0 else frame.copy()
    cv2.imwrite(str(OUTPUT_DIR / "01_initial" / f"{idx:02d}_initial.jpg"), init_disp)

    # 2. Vehicle detection
    t0 = time.time()
    vehicles = vehicle_detector.detect(frame)
    metrics["veh_time"] += time.time() - t0
    metrics["vehicles_detected"] += len(vehicles)

    # 3. Plate detection on FULL frame (multi-plate support)
    t0 = time.time()
    plate_dets = plate_detector.detect(frame)
    metrics["det_time"] += time.time() - t0

    # 4. OCR each plate crop
    plate_results = []
    for p in plate_dets:
        px1, py1, px2, py2 = p.bbox
        pad = int(0.06 * max(px2-px1, py2-py1))
        crop = frame[max(0, py1-pad):min(H, py2+pad), max(0, px1-pad):min(W, px2+pad)]
        if crop.size == 0:
            continue
        t0 = time.time()
        ocr = ocr_plate(crop)
        metrics["ocr_time"] += time.time() - t0
        if ocr:
            plate_results.append({"bbox": p.bbox, "det_conf": p.confidence,
                                  "text": ocr["text"], "ocr_conf": ocr["confidence"],
                                  "raw": ocr["raw"]})
            cv2.imwrite(str(OUTPUT_DIR / "04_plate_crops" /
                        f"{idx:02d}_plate{len(plate_results)-1}_{ocr['text'][:12]}.jpg"), crop)
    metrics["plates_detected"] += len(plate_results)

    # 5. Match plates to GT (IoU >= 0.5) and vehicles (containment)
    def plate_center(b):
        return ((b[0]+b[2])//2, (b[1]+b[3])//2)
    matched_gt = set()
    for pr in plate_results:
        best_iou, best_j = 0.0, -1
        for j, (gtext, gbox) in enumerate(gt_plates):
            if j in matched_gt:
                continue
            v = iou(pr["bbox"], gbox)
            if v > best_iou:
                best_iou, best_j = v, j
        if best_iou >= 0.5:
            matched_gt.add(best_j)
            gtext, _ = gt_plates[best_j]
            pr["gt_text"] = gtext
            pr["gt_iou"] = round(best_iou, 2)
            pr["exact"] = pr["text"].upper().replace(" ", "") == gtext.upper().replace(" ", "")
            metrics["plates_matched"] += 1
            if pr["exact"]:
                metrics["ocr_exact"] += 1
        else:
            pr["gt_text"] = None
            pr["gt_iou"] = round(best_iou, 2)
            pr["exact"] = False

    # 6. Associate each plate with nearest vehicle (center containment)
    for pr in plate_results:
        cx, cy = plate_center(pr["bbox"])
        pr["vehicle_idx"] = None
        for vi, veh in enumerate(vehicles):
            vx1, vy1, vx2, vy2 = veh.bbox
            if vx1 <= cx <= vx2 and vy1 <= cy <= vy2:
                pr["vehicle_idx"] = vi
                break

    # ------------------------------------------------------------------
    # 7. Final visualization: vehicle boxes + plate boxes + association
    # ------------------------------------------------------------------
    vis = frame.copy()
    th = max(2, W // 700)          # box thickness scales with resolution
    fs = max(0.6, W / 2600.0)      # font scale

    def draw_label(img, xy, text, color, fscale, thick):
        (tw, thh), bl = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fscale, thick)
        lx, ly = xy
        ly = max(thh + bl + 4, ly)
        cv2.rectangle(img, (lx, ly - thh - bl - 4), (lx + tw + 4, ly + 2), color, -1)
        cv2.putText(img, text, (lx + 2, ly), cv2.FONT_HERSHEY_SIMPLEX,
                    fscale, (255, 255, 255), thick, cv2.LINE_AA)

    # Vehicle boxes: green for matched vehicles, gray for unmatched
    matched_veh = {pr["vehicle_idx"] for pr in plate_results if pr["vehicle_idx"] is not None}
    for vi, veh in enumerate(vehicles):
        x1, y1, x2, y2 = veh.bbox
        color = VEHICLE_COLORS.get(veh.class_name, GREEN) if vi in matched_veh else (160, 160, 160)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, th)
        draw_label(vis, (x1, y1 - 6), f"{veh.class_name} {veh.confidence:.2f}", color, fs, max(1, th - 1))

    # Plate boxes: green = exact OCR match, red = no/incorrect GT match
    for pr in plate_results:
        x1, y1, x2, y2 = pr["bbox"]
        color = (60, 220, 60) if pr.get("exact") else (50, 50, 230)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, th)
        plabel = f"{pr['text']} {pr['ocr_conf']:.2f}"
        if pr.get("gt_text"):
            mark = "=" if pr["exact"] else "!="
            plabel += f" {mark}GT:{pr['gt_text']}"
        draw_label(vis, (x1, y2 + int(30 * fs) + 6), plabel, color, fs, max(1, th - 1))
        # association line plate -> vehicle
        if pr["vehicle_idx"] is not None:
            vx1, vy1, _, _ = vehicles[pr["vehicle_idx"]].bbox
            cv2.line(vis, plate_center(pr["bbox"]), (vx1 + 5, vy1 + 5), (255, 120, 0), 1)

    vscale = 1600.0 / W if W > 1600 else 1.0
    if vscale < 1.0:
        vis = cv2.resize(vis, (int(W * vscale), int(H * vscale)))
    cv2.imwrite(str(OUTPUT_DIR / "03_final" / f"{idx:02d}_final.jpg"), vis)


    # 8. Per-image bookkeeping
    n_matched = sum(1 for pr in plate_results if pr.get("gt_text") is not None)
    n_exact = sum(1 for pr in plate_results if pr.get("exact"))
    per_image.append({
        "idx": idx, "file": img_path.name, "vehicles": len(vehicles),
        "gt_plates": len(gt_plates), "plates_detected": len(plate_results),
        "matched": n_matched, "exact": n_exact,
    })
    results_json.append({
        "image": img_path.name,
        "vehicles": [{"class": v.class_name, "conf": round(v.confidence, 3),
                      "bbox": [int(c) for c in v.bbox]} for v in vehicles],
        "plates": [{"bbox": [int(c) for c in pr["bbox"]],
                    "det_conf": round(pr["det_conf"], 3),
                    "text": pr["text"], "ocr_conf": round(pr["ocr_conf"], 3),
                    "gt": pr.get("gt_text"), "gt_iou": pr.get("gt_iou"),
                    "exact": pr.get("exact")} for pr in plate_results],
    })
    status = "OK" if n_exact == len(gt_plates) and len(gt_plates) > 0 else "PARTIAL" if n_matched > 0 else "MISS"
    print(f"  [{idx:02d}] {img_path.name[:40]:40s} veh={len(vehicles)} "
          f"gt={len(gt_plates)} det={len(plate_results)} match={n_matched} exact={n_exact} [{status}]")

# ==============================================================================
# FINAL METRICS + REPORT
# ==============================================================================

print("\n" + "=" * 100)
print("FINAL METRICS")
print("=" * 100)

n_img = metrics["images"]
det_rate = 100.0 * metrics["plates_matched"] / metrics["gt_plates"] if metrics["gt_plates"] else 0
exact_of_gt = 100.0 * metrics["ocr_exact"] / metrics["gt_plates"] if metrics["gt_plates"] else 0
exact_of_matched = 100.0 * metrics["ocr_exact"] / metrics["plates_matched"] if metrics["plates_matched"] else 0
veh_per_img = metrics["vehicles_detected"] / n_img if n_img else 0
tot_time = metrics["veh_time"] + metrics["det_time"] + metrics["ocr_time"]

print(f"Images processed:        {n_img}")
print(f"Ground-truth plates:     {metrics['gt_plates']}")
print(f"Plates detected:         {metrics['plates_detected']}")
print(f"Plates matched (IoU>=0.5): {metrics['plates_matched']}  ->  Detection rate: {det_rate:.1f}%")
print(f"OCR exact matches:       {metrics['ocr_exact']}  ->  End-to-end accuracy: {exact_of_gt:.1f}%")
print(f"OCR accuracy (matched):  {exact_of_matched:.1f}%")
print(f"Avg vehicles/image:      {veh_per_img:.1f}")
print(f"Timing: veh {metrics['veh_time']:.1f}s | det {metrics['det_time']:.1f}s | ocr {metrics['ocr_time']:.1f}s | total {tot_time:.1f}s")

with open(OUTPUT_DIR / "results.json", "w", encoding="utf-8") as f:
    json.dump({"metrics": metrics, "per_image": per_image, "images": results_json}, f, indent=2)

# Per-image table for report
table = "| # | Image | Vehicles | GT Plates | Detected | Matched | Exact OCR |\n|---|-------|----------|-----------|----------|---------|------------|\n"
for r in per_image:
    table += (f"| {r['idx']:02d} | {r['file'][:32]} | {r['vehicles']} | {r['gt_plates']} | "
              f"{r['plates_detected']} | {r['matched']} | {r['exact']} |\n")

report = f"""# FINAL ANPR TEST - Indian Dataset (Fine-tuned YOLO + OnnxOCR PP-OCRv5)

**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Dataset:** DataCluster Indian Number Plates (full traffic scenes, 4032x3024)
**Images tested:** {n_img}

---

## End-to-End Metrics

| Metric | Value |
|--------|-------|
| Ground-truth plates | {metrics['gt_plates']} |
| Plates detected (IoU>=0.5 match) | {metrics['plates_matched']} / {metrics['gt_plates']} |
| **Plate detection rate** | **{det_rate:.1f}%** |
| OCR exact matches | {metrics['ocr_exact']} |
| **End-to-end accuracy (exact / GT)** | **{exact_of_gt:.1f}%** |
| **OCR accuracy on detected plates** | **{exact_of_matched:.1f}%** |
| Avg vehicles/image | {veh_per_img:.1f} |

## Pipeline Stages

| Stage | Model | Time |
|-------|-------|------|
| Vehicle detection | indian_traffic_yolov8 @ imgsz=1280, conf=0.10 | {metrics['veh_time']:.1f}s |
| Plate detection | license_finetuned.pt @ conf=0.20 (full-frame, multi-plate) | {metrics['det_time']:.1f}s |
| OCR | OnnxOCR (PaddleOCR PP-OCRv5, ONNXRuntime CPU) | {metrics['ocr_time']:.1f}s |

**OCR improvements in this run:** two-line plate reordering (Y-center row clustering),
position-aware character correction (Indian plate structure LL-DD-L(1-2)-DDDD).

## Per-Image Results

{table}

## Output Folders

- `01_initial/` - original images
- `02_intermediate/` - vehicle detections
- `03_final/` - annotated: vehicle class+conf, plate text+conf, association lines
- `04_plate_crops/` - each detected plate crop
- `results.json` - raw per-image data

---
*Generated by final_anpr_test_v2.py*
"""
with open(OUTPUT_DIR / "RESULTS.md", "w", encoding="utf-8") as f:
    f.write(report)
print(f"\nReport saved: {OUTPUT_DIR / 'RESULTS.md'}")
print(f"JSON saved:   {OUTPUT_DIR / 'results.json'}")
print("DONE")
