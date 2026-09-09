"""
Model 2 — ByteTracker main tracker class.
Uses IoU-based greedy matching for multi-object tracking.
"""

import logging
import numpy as np
from typing import List, Dict, Any, Tuple

from pipeline.tracking.track import Track
from pipeline.config import MATCH_THRESH, TRACK_HIGH_THRESH, TRACK_LOW_THRESH, MAX_TIME_LOST

logger = logging.getLogger("sentinel.tracking")


def _iou(bbox1: List[float], bbox2: List[float]) -> float:
    """Compute IoU between two bounding boxes [x1,y1,x2,y2]."""
    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[2], bbox2[2])
    y2 = min(bbox1[3], bbox2[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0.0


class ByteTracker:
    """Simplified ByteTrack for vehicle tracking."""

    def __init__(
        self,
        high_thresh: float = TRACK_HIGH_THRESH,
        low_thresh: float = TRACK_LOW_THRESH,
        match_thresh: float = MATCH_THRESH,
        max_time_lost: int = MAX_TIME_LOST,
    ):
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.match_thresh = match_thresh
        self.max_time_lost = max_time_lost
        self.tracks: List[Track] = []
        self.frame_count = 0
        logger.info("ByteTracker initialized.")

    def _iou_matrix(self, detections: List[Dict], tracks: List[Track]) -> np.ndarray:
        if not detections or not tracks:
            return np.empty((len(detections), len(tracks)))
        mat = np.zeros((len(detections), len(tracks)))
        for d, det in enumerate(detections):
            for t, trk in enumerate(tracks):
                mat[d, t] = _iou(det["bbox"], trk.bbox)
        return mat

    def _linear_assignment(self, cost_matrix: np.ndarray, thresh: float):
        if cost_matrix.size == 0:
            return [], list(range(cost_matrix.shape[0])), list(range(cost_matrix.shape[1]))
        matched, ud, ut = [], list(range(cost_matrix.shape[0])), list(range(cost_matrix.shape[1]))
        while cost_matrix.size > 0:
            mx = cost_matrix.max()
            if mx < thresh:
                break
            d, t = np.unravel_index(cost_matrix.argmax(), cost_matrix.shape)
            matched.append((d, t))
            ud.remove(d)
            ut.remove(t)
            cost_matrix[d, :] = 0
            cost_matrix[:, t] = 0
        return matched, ud, ut

    def update(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self.frame_count += 1
        dets_h = [d for d in detections if d["confidence"] >= self.high_thresh]
        dets_l = [d for d in detections if self.low_thresh <= d["confidence"] < self.high_thresh]
        confirmed = [t for t in self.tracks if t.is_confirmed()]
        unconfirmed = [t for t in self.tracks if not t.is_confirmed()]

        ma, uda, uta = self._linear_assignment(self._iou_matrix(dets_h, confirmed), self.match_thresh)
        for d, t in ma:
            confirmed[t].update(dets_h[d])

        rd = [dets_h[i] for i in uda] + dets_l
        rt = [confirmed[i] for i in uta]
        mb, udb, utb = self._linear_assignment(self._iou_matrix(rd, rt), self.match_thresh)
        for d, t in mb:
            rt[t].update(rd[d])
        for t in utb:
            rt[t].mark_missed()

        rh = [dets_h[i] for i in uda if i not in [m[0] for m in ma]]
        mc, udc, _ = self._linear_assignment(self._iou_matrix(rh, unconfirmed), self.match_thresh)
        for d, t in mc:
            unconfirmed[t].update(rh[d])
        for t in range(len(unconfirmed)):
            if t not in [m[1] for m in mc]:
                unconfirmed[t].mark_missed()

        for d in udc:
            self.tracks.append(Track(rh[d]))

        self.tracks = [t for t in self.tracks if not t.is_deleted()]

        out = []
        for trk in self.tracks:
            if trk.is_confirmed() and trk.time_since_update == 0:
                out.append({
                    "bbox": trk.bbox,
                    "confidence": trk.confidence,
                    "class_id": trk.class_id,
                    "class_name": trk.class_name,
                    "track_id": trk.track_id,
                    "color": trk.color,
                    "area": (trk.bbox[2] - trk.bbox[0]) * (trk.bbox[3] - trk.bbox[1]),
                })
        return out

    def reset(self):
        self.tracks.clear()
        self.frame_count = 0
        Track._next_id = 0
