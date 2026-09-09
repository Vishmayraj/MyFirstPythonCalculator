"""
Phase 1 — In-Frame IoU Tracker & De-duplicator
===============================================
Tracks vehicles across consecutive frames within a single camera FOV.
Emits exactly ONE TrackedEvent per physical vehicle appearance.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from pipeline.detection.vehicle_detector import RawDetection

logger = logging.getLogger("sentinel.tracker")
logger.setLevel(logging.INFO)


def _match_score(box_a: Tuple[int,int,int,int], box_b: Tuple[int,int,int,int]) -> float:
    """Combines IoU with spatial centroid distance for rock-solid tracking on fast vehicles."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
    inter = iw * ih

    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union  = area_a + area_b - inter
    iou = inter / union if union > 0 else 0.0

    if iou >= 0.15:
        return iou

    # Centroid distance fallback for fast moving vehicles
    ca_x = (ax1 + ax2) / 2.0; ca_y = (ay1 + ay2) / 2.0
    cb_x = (bx1 + bx2) / 2.0; cb_y = (by1 + by2) / 2.0
    dist = ((ca_x - cb_x)**2 + (ca_y - cb_y)**2) ** 0.5
    scale = max(ax2 - ax1, ay2 - ay1, bx2 - bx1, by2 - by1, 30)

    if dist < scale * 1.6:
        return max(0.12, 0.4 * (1.0 - (dist / (scale * 1.6))))
    return 0.0


@dataclass
class _Track:
    track_id:       int
    class_id:       int
    class_name:     str
    bbox:           Tuple[int,int,int,int]
    best_conf:      float
    best_crop:      Optional[np.ndarray]
    first_pts_ms:   float
    last_pts_ms:    float
    frame_count:    int = 1
    missed_frames:  int = 0
    persisted:      bool = False
    sighting_id:    str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TrackedEvent:
    """
    Emitted EXACTLY ONCE per physical vehicle visit to a camera.
    Carries the best crop + metadata for DB persistence.
    """
    sighting_id:  str
    track_id:     int
    camera_id:    str
    class_name:   str
    confidence:   float
    bbox:         Tuple[int,int,int,int]
    crop:         Optional[np.ndarray]
    pts_ms:       float
    timestamp:    datetime


class InFrameTracker:
    """
    IoU + Proximity single-camera tracker with track ID consistency.

    Parameters
    ----------
    camera_id:           Identifies which camera this tracker belongs to.
    min_confirmed_frames: Consecutive frames before a track is confirmed & persisted.
    max_missed_frames:    How many consecutive frames without a match before archiving.
    iou_threshold:        Minimum score to link a detection to an existing track.
    """

    def __init__(
        self,
        camera_id: str,
        min_confirmed_frames: int = 2,
        max_missed_frames: int = 20,
        iou_threshold: float = 0.12,
    ):
        self.camera_id           = camera_id
        self.min_confirmed_frames = min_confirmed_frames
        self.max_missed_frames   = max_missed_frames
        self.iou_threshold       = iou_threshold

        self._next_id: int = 1
        self.active_tracks: Dict[int, _Track] = {}

    def update(
        self,
        detections: List[RawDetection],
        pts_ms: float = 0.0,
        current_time: Optional[datetime] = None,
    ) -> Tuple[List[TrackedEvent], List[_Track]]:
        """
        Feed one frame's detections into the tracker.
        Returns:
          new_events: Newly confirmed vehicles (emitted once)
          active: All active tracks with persistent track IDs
        """
        now = current_time or datetime.now(timezone.utc)
        new_events: List[TrackedEvent] = []

        # ── Step 1: Matching ───────────────────────────────────────
        track_ids    = list(self.active_tracks.keys())
        unmatched_det_idxs = list(range(len(detections)))
        matched_track_ids: set = set()

        if track_ids and detections:
            score_mat = np.zeros((len(track_ids), len(detections)), dtype=np.float32)
            for i, tid in enumerate(track_ids):
                trk = self.active_tracks[tid]
                for j, det in enumerate(detections):
                    s = _match_score(trk.bbox, det.bbox)
                    # Class consistency bonus
                    if trk.class_name.lower() == det.class_name.lower():
                        s += 0.08
                    score_mat[i, j] = s

            # Greedily consume best matches above threshold
            while True:
                best = float(score_mat.max()) if score_mat.size else 0.0
                if best < self.iou_threshold:
                    break
                i, j = map(int, np.unravel_index(score_mat.argmax(), score_mat.shape))
                tid  = track_ids[i]
                det  = detections[j]
                trk  = self.active_tracks[tid]

                # Smooth bounding box (EMA) to avoid frame jitter
                trk.bbox = (
                    int(0.80 * det.bbox[0] + 0.20 * trk.bbox[0]),
                    int(0.80 * det.bbox[1] + 0.20 * trk.bbox[1]),
                    int(0.80 * det.bbox[2] + 0.20 * trk.bbox[2]),
                    int(0.80 * det.bbox[3] + 0.20 * trk.bbox[3]),
                )
                trk.frame_count  += 1
                trk.missed_frames = 0
                trk.last_pts_ms   = pts_ms

                if det.confidence > trk.best_conf:
                    trk.best_conf = det.confidence
                    trk.best_crop = det.crop
                    trk.class_name = det.class_name

                matched_track_ids.add(tid)
                if j in unmatched_det_idxs:
                    unmatched_det_idxs.remove(j)

                score_mat[i, :] = -1.0   # invalidate row
                score_mat[:, j] = -1.0   # invalidate column

        # ── Step 2: Age unmatched tracks ───────────────────────────
        to_delete = []
        for tid, trk in self.active_tracks.items():
            if tid not in matched_track_ids:
                trk.missed_frames += 1
                if trk.missed_frames > self.max_missed_frames:
                    to_delete.append(tid)

        # ── Step 3: Create new tracks for unmatched detections ─────
        for j in unmatched_det_idxs:
            det = detections[j]
            self.active_tracks[self._next_id] = _Track(
                track_id     = self._next_id,
                class_id     = det.class_id,
                class_name   = det.class_name,
                bbox         = det.bbox,
                best_conf    = det.confidence,
                best_crop    = det.crop,
                first_pts_ms = pts_ms,
                last_pts_ms  = pts_ms,
            )
            self._next_id += 1

        # ── Step 4: Emit confirmation events (exactly once) ────────
        for trk in self.active_tracks.values():
            if not trk.persisted and trk.frame_count >= self.min_confirmed_frames:
                trk.persisted = True
                new_events.append(TrackedEvent(
                    sighting_id = trk.sighting_id,
                    track_id    = trk.track_id,
                    camera_id   = self.camera_id,
                    class_name  = trk.class_name,
                    confidence  = trk.best_conf,
                    bbox        = trk.bbox,
                    crop        = trk.best_crop,
                    pts_ms      = trk.first_pts_ms,
                    timestamp   = now,
                ))
                logger.info(
                    f"[{self.camera_id}] Confirmed track #{trk.track_id} "
                    f"({trk.class_name} conf={trk.best_conf:.2f}) → 1 DB event."
                )

        # ── Step 5: Evict dead tracks ───────────────────────────────
        for tid in to_delete:
            del self.active_tracks[tid]

        return new_events, list(self.active_tracks.values())
