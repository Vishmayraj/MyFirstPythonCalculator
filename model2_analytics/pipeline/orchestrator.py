"""
Model 2 — Main AI Pipeline Orchestrator
Ties together: ingestion → detection → tracking → annotation → output.
"""

import logging
import sys
from pathlib import Path

import cv2
import numpy as np

# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import (
    CAM04_RTSP,
    LIVE_SCREENSHOTS_DIR,
    CONFIDENCE_THRESHOLD,
    VEHICLE_CLASSES,
)
from pipeline.detection.yolo_detector import VehicleDetector
from pipeline.tracking.byte_tracker import ByteTracker
from pipeline.ingest import StreamIngestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("sentinel.pipeline")

CLASS_COLORS = {
    "car": (0, 255, 0),
    "truck": (255, 0, 0),
    "bus": (0, 165, 255),
    "van": (255, 255, 0),
    "motorcycle": (255, 0, 255),
}


class VehicleAnalyticsPipeline:
    """
    End-to-end vehicle analytics pipeline.
    ingest → detect → track → annotate → output
    """

    def __init__(self, confidence=CONFIDENCE_THRESHOLD, device="auto"):
        logger.info("Initializing Vehicle Analytics Pipeline...")
        self.detector = VehicleDetector(confidence=confidence, device=device)
        self.tracker = ByteTracker()
        self.frame_count = 0
        self.total_detections = 0
        logger.info("Pipeline ready.")

    def process_frame(self, frame: np.ndarray) -> tuple:
        """
        Process a single frame through the pipeline.

        Returns:
            (annotated_frame, tracked_detections)
        """
        detections = self.detector.detect(frame)
        tracked = self.tracker.update(detections)
        annotated = self._annotate(frame, tracked)
        self.frame_count += 1
        self.total_detections += len(tracked)
        return annotated, tracked

    def _annotate(self, frame, tracked_dets):
        """Draw bounding boxes, labels, track IDs on frame."""
        img = frame.copy()
        for det in tracked_dets:
            x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
            cls_name = det.get("class_name", "unknown")
            conf = det.get("confidence", 0)
            track_id = det.get("track_id", -1)
            color = det.get("color", CLASS_COLORS.get(cls_name, (0, 255, 0)))

            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            label = f"ID:{track_id} {cls_name} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (x1, y1 - th - 10), (x1 + tw + 4, y1), color, -1)
            cv2.putText(img, label, (x1 + 2, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        cv2.putText(img, f"YOLO26m + ByteTrack | Frame: {self.frame_count} | Vehicles: {len(tracked_dets)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        return img

    def run_on_camera(self, camera_id="cam04", rtsp_url=None, max_frames=100, save_every=10):
        """Run pipeline on a live camera feed."""
        if rtsp_url is None:
            rtsp_url = CAM04_RTSP

        client = StreamIngestClient(camera_id=camera_id, rtsp_url=rtsp_url)
        logger.info(f"Running pipeline on {camera_id}: {rtsp_url}")

        saved_paths = []
        try:
            for frame, pts_ms in client.read_frames():
                if self.frame_count >= max_frames:
                    break
                annotated, tracked = self.process_frame(frame)

                if self.frame_count % save_every == 0:
                    save_path = LIVE_SCREENSHOTS_DIR / f"pipeline_{camera_id}_{self.frame_count:04d}.jpg"
                    cv2.imwrite(str(save_path), annotated)
                    saved_paths.append(str(save_path))
                    logger.info(f"  Frame {self.frame_count}: {len(tracked)} vehicles | Saved: {save_path.name}")
        except KeyboardInterrupt:
            logger.info("Pipeline interrupted.")
        except Exception as e:
            logger.error(f"Pipeline error: {e}")

        logger.info(f"Pipeline complete. {self.frame_count} frames, {self.total_detections} total detections.")
        return saved_paths

    def run_on_image(self, image_path: str) -> tuple:
        """Run pipeline on a single image file."""
        frame = cv2.imread(image_path)
        if frame is None:
            raise FileNotFoundError(f"Could not load image: {image_path}")
        annotated, tracked = self.process_frame(frame)
        return annotated, tracked

    def reset(self):
        """Reset pipeline state."""
        self.tracker.reset()
        self.frame_count = 0
        self.total_detections = 0


if __name__ == "__main__":
    pipeline = VehicleAnalyticsPipeline()
    pipeline.run_on_camera()
