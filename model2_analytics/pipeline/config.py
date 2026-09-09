"""
Model 2 — AI Pipeline Configuration
All paths, thresholds, and model settings centralized here.
"""

import os
from pathlib import Path

# ── Repository root ──────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ── Pipeline paths ───────────────────────────────────────────────
PIPELINE_DIR = Path(__file__).resolve().parent
DETECTION_DIR = PIPELINE_DIR / "detection"
TRACKING_DIR = PIPELINE_DIR / "tracking"
PLATE_DIR = PIPELINE_DIR / "plate"
OCR_DIR = PIPELINE_DIR / "ocr"
REID_DIR = PIPELINE_DIR / "reid"
INGESTION_DIR = PIPELINE_DIR / "ingestion"
FUSION_DIR = PIPELINE_DIR / "fusion"
EVENTS_DIR = PIPELINE_DIR / "events"

# ── Weights (configurable via env var; default: repo-root/weights) ──────────
WEIGHTS_DIR = Path(os.getenv("MODEL2_WEIGHTS_DIR", str(REPO_ROOT / "weights"))).expanduser()
YOLO26M_PRETRAINED = "yolo26m.pt"  # Official Ultralytics checkpoint
FINETUNED_WEIGHTS = WEIGHTS_DIR / "yolo26m_vehicles_best.pt"

# ── Output paths (inside repo) ───────────────────────────────────
DEMO_RESULTS_DIR = REPO_ROOT / "demo_results"
EVAL_SCREENSHOTS_DIR = DEMO_RESULTS_DIR / "eval_screenshots"
LIVE_SCREENSHOTS_DIR = DEMO_RESULTS_DIR / "live_screenshots"
METRICS_DIR = DEMO_RESULTS_DIR / "metrics"

# ── Dataset ──────────────────────────────────────────────────────
HF_DATASET_NAME = "Francesco/vehicles-q0x2v"
DATASET_DIR = PIPELINE_DIR / "dataset"
DATASET_YAML = PIPELINE_DIR / "dataset.yaml"

# ── YOLO26m Detection settings ───────────────────────────────────
CONFIDENCE_THRESHOLD = 0.25
IOU_THRESHOLD = 0.45
IMG_SIZE = 640
MAX_DETECTIONS = 300

# ── Vehicle class mapping for vehicles-q0x2v ─────────────────────
# Dataset has 12 classes (IDs 1-12 in COCO format, 0-11 in YOLO format)
# ClassLabel names: ['vehicles', 'big bus', 'big truck', 'bus-l-', 'bus-s-',
#   'car', 'mid truck', 'small bus', 'small truck', 'truck-l-', 'truck-m-',
#   'truck-s-', 'truck-xl-']
# Note: class 0 ('vehicles') is not used in actual data, so we map 1-12 → 0-11
VEHICLE_CLASSES = {
    0: "big bus",
    1: "big truck",
    2: "bus-l-",
    3: "bus-s-",
    4: "car",
    5: "mid truck",
    6: "small bus",
    7: "small truck",
    8: "truck-l-",
    9: "truck-m-",
    10: "truck-s-",
    11: "truck-xl-",
}
NUM_CLASSES = 12
# Original dataset class IDs (1-12) to YOLO class IDs (0-11)
DATASET_TO_YOLO_CLASS = {i: i - 1 for i in range(1, 13)}

# ── Tracking settings ────────────────────────────────────────────
# cam04 is blurry/pixelated → detections come in at low confidence (0.1-0.45)
# so we lower the thresholds to create tracks from these detections.
TRACK_BUFFER = 30
TRACK_HIGH_THRESH = 0.15
TRACK_LOW_THRESH = 0.05
MATCH_THRESH = 0.6
MAX_TIME_LOST = 30

# ── CCTV Camera settings ─────────────────────────────────────────
CAM04_RTSP = "rtsp://103.250.160.189:8554/stream/cam04"
CAM04_HLS = "https://cctv.corp8.cloud/cam04/index.m3u8"
# NOTE: no per-camera credential here on purpose. Neither CAM04_RTSP nor
# CAM04_HLS above embeds a username/password, and nothing in the codebase
# ever reads a per-camera secret for cam04 -- the grid-wide credential
# (GRID_RTSP_USER/GRID_RTSP_PASS, see model1-registry/app/config.py and
# shared/adapters/factory.py) is what's actually used end to end. A prior
# fix for this finding introduced a standalone CAM04_PASSWORD env var, but
# that duplicated a credential slot that already exists (GRID_RTSP_PASS)
# and still had no real call site, so it's been removed rather than kept
# around unused. See AuditReport2.md finding 1.

# ── Training settings ────────────────────────────────────────────
EPOCHS = 30
BATCH_SIZE = 16
LEARNING_RATE = 0.001
PATIENCE = 10  # early stopping patience

# ── Create output directories ────────────────────────────────────
for _dir in [WEIGHTS_DIR, DEMO_RESULTS_DIR, EVAL_SCREENSHOTS_DIR, LIVE_SCREENSHOTS_DIR, METRICS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)
