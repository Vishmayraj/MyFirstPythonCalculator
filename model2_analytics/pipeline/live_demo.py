"""
Model 2 — Live Demo on CCTV cam04
Runs YOLO26m detection + ByteTrack tracking on the live camera feed.
Saves annotated screenshots as proof of working.
"""

import logging
import time
import sys
from pathlib import Path

import cv2
import numpy as np

# Setup path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import (
    CAM04_RTSP,
    LIVE_SCREENSHOTS_DIR,
    VEHICLE_CLASSES,
    CONFIDENCE_THRESHOLD,
)
from pipeline.detection.yolo_detector import VehicleDetector
from pipeline.tracking.byte_tracker import ByteTracker
from pipeline.ingest import StreamIngestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("sentinel.live_demo")

# Color palette
CLASS_COLORS = {
    "car": (0, 255, 0),
    "truck": (255, 0, 0),
    "bus": (0, 165, 255),
    "van": (255, 255, 0),
    "motorcycle": (255, 0, 255),
}


def draw_tracked_detections(frame, tracked_dets):
    """Draw tracked bounding boxes with IDs, labels, and motion trails."""
    img = frame.copy()
    for det in tracked_dets:
        x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
        cls_name = det.get("class_name", "unknown")
        conf = det.get("confidence", 0)
        track_id = det.get("track_id", -1)
        color = det.get("color", CLASS_COLORS.get(cls_name, (0, 255, 0)))

        # Bounding box
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

        # Label background
        label = f"ID:{track_id} {cls_name} {conf:.0%}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(img, (x1, y1 - th - 10), (x1 + tw + 4, y1), color, -1)
        cv2.putText(img, label, (x1 + 2, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    # Add info overlay
    cv2.putText(img, f"YOLO26m + ByteTrack | Detections: {len(tracked_dets)}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return img


def enhance_frame(frame):
    """
    Enhance blurry/pixelated CCTV frames for better detection.
    Applies CLAHE contrast enhancement + mild unsharp masking.
    """
    # Convert BGR → LAB, apply CLAHE to L channel
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l)
    enhanced = cv2.merge((l_enhanced, a, b))
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    # Mild unsharp mask
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.5)
    sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)
    return sharpened


def run_live_demo(camera_id="cam04", rtsp_url=None, num_frames=150, save_every=10):
    """
    Run live detection + tracking demo on a camera feed.

    Args:
        camera_id: Camera identifier.
        rtsp_url: RTSP URL. Defaults to cam04.
        num_frames: Number of frames to process.
        save_every: Save screenshot every N frames.
    """
    if rtsp_url is None:
        rtsp_url = CAM04_RTSP

    logger.info("=" * 60)
    logger.info("Model 2 — Live Demo: YOLO26m + ByteTrack on CCTV cam04")
    logger.info("=" * 60)

    # Initialize detector and tracker with low thresholds for blurry feed
    detector = VehicleDetector(confidence=0.08)
    tracker = ByteTracker()

    # Initialize stream
    client = StreamIngestClient(camera_id=camera_id, rtsp_url=rtsp_url)

    logger.info(f"Connecting to: {rtsp_url}")
    logger.info(f"Processing {num_frames} frames, saving every {save_every} frames...")

    frame_count = 0
    total_detections = 0
    frames_with_vehicles = 0

    try:
        for frame, pts_ms in client.read_frames():
            if frame_count >= num_frames:
                break

            # Enhance blurry feed for better detection
            enhanced = enhance_frame(frame)

            # Detect vehicles
            detections = detector.detect(enhanced)

            # Track vehicles
            tracked = tracker.update(detections)
            total_detections += len(tracked)
            if len(tracked) > 0:
                frames_with_vehicles += 1

            # Draw results on the ORIGINAL frame (not enhanced) for clean output
            annotated = draw_tracked_detections(frame, tracked)

            # Save screenshot
            if frame_count % save_every == 0:
                save_path = LIVE_SCREENSHOTS_DIR / f"live_{camera_id}_{frame_count:04d}.jpg"
                cv2.imwrite(str(save_path), annotated)
                logger.info(f"  Frame {frame_count}: {len(tracked)} vehicles tracked | Saved: {save_path.name}")

            frame_count += 1

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.error(f"Error during live demo: {e}")

    logger.info("=" * 60)
    logger.info(f"Live demo complete!")
    logger.info(f"  Frames processed: {frame_count}")
    logger.info(f"  Total detections: {total_detections}")
    logger.info(f"  Frames with vehicles: {frames_with_vehicles} ({frames_with_vehicles/max(frame_count,1)*100:.0f}%)")
    logger.info(f"  Screenshots saved to: {LIVE_SCREENSHOTS_DIR}")
    logger.info("=" * 60)

    return frame_count, total_detections


if __name__ == "__main__":
    run_live_demo()
