"""
Phase 3 — ANPR Video Processor
===============================
End-to-end video pipeline:
    frame → vehicle_detect (indian_traffic_yolov8.pt)
          → plate_detect   (license_plate_detector.pt)
          → OCR            (EasyOCR)
          → watchlist check + alerts (PostgreSQL)
          → annotated output video

Run:
    python -m pipeline.anpr_video_processor
        --video "ANPR bhidio/13052823_3840_2160_30fps.mp4"
        --output "output/anpr_annotated.mp4"
        [--frames N]        # process only first N frames
        [--stride 2]        # process every Nth frame
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── Path bootstrap ──
_PROJECT = Path(__file__).resolve().parents[1]     # model2-analytics
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

logger = logging.getLogger("sentinel.anpr_video")
logger.setLevel(logging.INFO)

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )


@dataclass
class FrameResult:
    frame_idx:    int
    vehicles:     list                          # [(bbox, class, conf), ...]
    plates:       list                          # [(bbox, conf), ...]
    assigned:     list                          # [(vbbox, vclass, plate_text, plate_conf), ...]
    watchlisted:  list                          # matching entries
# ── Main processor ──
class ANPRVideoProcessor:
    """Detects vehicles + plates on a video, produces annotated output."""

    def __init__(
        self,
        video_path: str,
        output_path: str,
        process_stride: int = 2,
        max_frames: Optional[int] = None,
        conf_vehicle: float = 0.40,
        conf_plate: float = 0.40,
        db_url: Optional[str] = None,
    ):
        self.video_path      = video_path
        self.output_path     = output_path
        self.process_stride  = max(1, process_stride)
        self.max_frames      = max_frames
        self.db_url          = db_url

        self._conf           = conf_vehicle
        self._conf_plate     = conf_plate

        # Lazy state — built inside run()
        self.vehicle_detector = None
        self.anpr            = None       # ANPRPipeline (plate+OCR)
        self.anpr_detector   = None
        self.ocr_engine      = None
        self.watchlist       = None
        self.alert_service   = None

        self.results_csv: List[dict] = []

    # ── Model init ──
    def _init_models(self):
        from pipeline.detection.vehicle_detector import VehicleDetector
        from pipeline.plate.plate_detector import PlateDetector
        from pipeline.ocr.ocr_engine import EasyOCREngine

        logger.info("Loading vehicle detector (indian_traffic_yolov8.pt)…")
        self.vehicle_detector = VehicleDetector(
            confidence_threshold=self._conf,
            iou_threshold=0.45,
        )

        logger.info("Loading plate detector (license_plate_detector.pt)…")
        self.anpr_detector = PlateDetector(
            confidence_threshold=self._conf_plate,
        )

        logger.info("Loading OCR engine (EasyOCR)…")
        self.ocr_engine = EasyOCREngine(gpu=False)

    # ── Main run loop ──
    def run(self) -> str:
        import cv2

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self.video_path}")

        fps    = cap.get(cv2.CAP_PROP_FPS) or 30
        width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        logger.info(f"Video: {width}x{height} @ {fps}fps, {total} frames")

        self._init_models()

        # Video writer
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(self.output_path, fourcc, fps // self.process_stride, (width, height))

        frame_idx = 0
        stats = {"vehicles": 0, "plates": 0, "ocr_reads": 0, "watchlist_hits": 0}

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if self.max_frames and frame_idx > self.max_frames:
                    break
                if (frame_idx - 1) % self.process_stride != 0:
                    continue

                annotated, fstats = self._process_frame(frame, frame_idx)
                out.write(annotated)

                stats["vehicles"]     += fstats["vehicles"]
                stats["plates"]       += fstats["plates"]
                stats["ocr_reads"]    += fstats["ocr_reads"]
                stats["watchlist_hits"] += fstats["watchlist_hits"]

                if frame_idx % 100 == 0:
                    logger.info(f"Frame {frame_idx}: {fstats}")

        finally:
            cap.release()
            out.release()

        # Write CSV
        csv_path = Path(self.output_path).with_suffix(".csv")
        self._write_csv(str(csv_path))

        logger.info(f"Done. Stats: {stats}")
        return self.output_path

    # ── Per-frame processing ──
    def _process_frame(self, frame, frame_idx: int):
        import cv2

        stats = {"vehicles": 0, "plates": 0, "ocr_reads": 0, "watchlist_hits": 0}
        annotated = frame.copy()

        # 1) Vehicle detection
        detections = self.vehicle_detector.detect(frame)
        stats["vehicles"] = len(detections)

        # 2) Plate detection + OCR — batch mode
        vehicle_boxes = [d.bbox for d in detections]
        plate_results = {}   # bbox_tuple -> PlateResult
        if self.anpr is not None and vehicle_boxes:
            plate_results = self.anpr.process_frame(frame, vehicle_boxes)

        # 3) Draw annotations
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            cls = det.class_name
            conf = det.confidence

            res = plate_results.get(tuple(det.bbox))
            if res is not None:
                stats["ocr_reads"] += 1
                color = (0, 255, 0)            # green — plate read
                label = f"{cls} {conf:.0%}  [{res.plate_text}]"
            else:
                color = (255, 165, 0)          # orange — no plate read
                label = f"{cls} {conf:.0%}"

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                annotated,
                label,
                (x1, max(24, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2,
            )

        cv2.putText(
            annotated,
            f"Frame {frame_idx}",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2,
        )

        # CSV row
        for det in detections:
            res = plate_results.get(tuple(det.bbox))
            self.results_csv.append({
                "frame": frame_idx,
                "class": det.class_name,
                "conf": round(det.confidence, 4),
                "bbox": str(det.bbox),
                "plate": res.plate_text if res else "",
                "plate_conf": round(res.confidence, 4) if res else "",
            })

        return annotated, stats

    def _write_csv(self, path: str):
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["frame", "class", "conf", "bbox", "plate", "plate_conf"],
            )
            writer.writeheader()
            writer.writerows(self.results_csv)


# ── CLI ──
def main():
    parser = argparse.ArgumentParser(description="ANPR Video Processor")
    parser.add_argument("--video", required=True, help="Input video path")
    parser.add_argument("--output", default="output/anpr_annotated.mp4")
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--frames", type=int, default=None,
                        help="Process only first N frames")
    parser.add_argument("--db-url", default=None,
                        help="PostgreSQL URL for watchlist checks")
    args = parser.parse_args()

    proc = ANPRVideoProcessor(
        video_path=args.video,
        output_path=args.output,
        process_stride=args.stride,
        max_frames=args.frames,
        db_url=args.db_url,
    )
    out = proc.run()
    print(f"\n✓ Annotated video: {out}")
    print(f"   CSV log: {Path(out).with_suffix('.csv')}")


if __name__ == "__main__":
    main()