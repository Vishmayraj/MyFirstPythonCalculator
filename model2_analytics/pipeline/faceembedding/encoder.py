"""
Pretrained 512-Dimensional Face Embedding Engine for Sentinel Model 2.

Implements:
  1. InceptionResnetV1 (VGGFace2 backbone) feature extractor.
  2. 5-point landmark affine face alignment (canonical eye alignment).
  3. Preprocessing (160x160 RGB, fixed standardization).
  4. L2-normalized 512-dimensional output vector (matching pgvector VECTOR(512)).
  5. Cosine similarity and distance helpers for 1:N watchlist matching.
"""

import math
import os
import threading
import logging
from pathlib import Path
from typing import Optional, List, Tuple, Union

import cv2
import numpy as np
import torch
from facenet_pytorch import InceptionResnetV1

logger = logging.getLogger("sentinel.face.encoder")

DEFAULT_WEIGHTS_DIR = Path(__file__).resolve().parents[2] / "weights"

# Baseline similarity thresholds for 1:N face verification.
# TODO: Calibrate against validation set; placeholder only.
# NOTE: Operational thresholds for law-enforcement / surveillance matching must be calibrated
# via empirical ROC/DET curves on target operational CCTV footage based on required False Accept
# Rate (FAR) and False Reject Rate (FRR). These default constants serve solely as initial placeholders.
DEFAULT_SIMILARITY_THRESHOLD: float = 0.70  # Placeholder baseline, pending empirical ROC calibration
DEFAULT_COSINE_DISTANCE_THRESHOLD: float = 0.30  # Placeholder baseline (1.0 - 0.70)


class FaceEmbeddingEngine:
    """
    Extracts canonical, L2-normalized 512-dimensional face embeddings using InceptionResnetV1.
    """

    def __init__(
        self,
        weights_dir: Optional[Path] = None,
        device: Optional[str] = None,
        model_name: str = "vggface2",
    ):
        self.weights_dir = Path(weights_dir) if weights_dir else DEFAULT_WEIGHTS_DIR
        self.weights_dir.mkdir(parents=True, exist_ok=True)

        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.model_name = model_name
        self._lock = threading.Lock()
        self._model: Optional[InceptionResnetV1] = None

        logger.info(f"Initializing FaceEmbeddingEngine on device: {self.device}")
        self._load_model()

    def _load_model(self) -> None:
        """Load pretrained InceptionResnetV1 with weights cached in weights_dir."""
        # Set torch cache directory to our persistent weights directory
        os.environ["TORCH_HOME"] = str(self.weights_dir)
        try:
            with self._lock:
                self._model = InceptionResnetV1(
                    pretrained=self.model_name,
                    classify=False,
                ).eval().to(self.device)
            logger.info(f"InceptionResnetV1 ({self.model_name}) 512-d model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load InceptionResnetV1: {e}")
            raise

    def align_face(
        self,
        img: np.ndarray,
        landmarks: np.ndarray,
        desired_size: int = 160,
    ) -> np.ndarray:
        """
        Align face so that both eye pupils are horizontally level at canonical coordinates.

        Landmarks order:
          0: right eye pupil (subject's right, left on image)
          1: left eye pupil  (subject's left, right on image)
          2: nose tip
          3: right mouth corner
          4: left mouth corner

        IMPORTANT:
          Landmarks MUST be in the exact coordinate space of `img`.
          If passing the original image, use the original detector landmarks.
          If passing a cropped face, landmarks must be shifted by [-x1, -y1].
        """
        if landmarks is None or len(landmarks) < 2:
            return cv2.resize(img, (desired_size, desired_size), interpolation=cv2.INTER_AREA)

        # ── Landmark Bounds Validation ──────────────────────────────────────
        # Defensive check: ensure all landmarks fall within the passed image's canvas.
        # This immediately catches coordinate-space mismatches (e.g., passing a cropped
        # face alongside full-canvas landmark coordinates).
        h, w = img.shape[:2]
        lm_arr = np.asarray(landmarks, dtype=np.float64)
        if np.any(lm_arr[:, 0] < 0) or np.any(lm_arr[:, 0] >= w) or \
           np.any(lm_arr[:, 1] < 0) or np.any(lm_arr[:, 1] >= h):
            raise ValueError(
                f"Landmark coordinates fall outside image bounds (dimensions: {w}x{h}). "
                f"Landmarks min=({lm_arr[:, 0].min():.1f}, {lm_arr[:, 1].min():.1f}), "
                f"max=({lm_arr[:, 0].max():.1f}, {lm_arr[:, 1].max():.1f}). "
                "Landmarks must be in the coordinate space of the passed image (pass original full image, not crop)."
            )

        # Eye centers
        r_eye = lm_arr[0]
        l_eye = lm_arr[1]

        # Angle between eyes
        dx = float(l_eye[0] - r_eye[0])
        dy = float(l_eye[1] - r_eye[1])
        angle = math.degrees(math.atan2(dy, dx))

        # Center point between eyes
        eye_center = ((r_eye[0] + l_eye[0]) * 0.5, (r_eye[1] + l_eye[1]) * 0.5)

        # Current eye distance
        current_dist = math.hypot(dx, dy)
        # In a 160x160 crop, canonical distance between eyes is ~50 px (31% of image width)
        desired_dist = desired_size * 0.32
        scale = desired_dist / max(current_dist, 1e-4)

        # Compute affine rotation matrix
        M = cv2.getRotationMatrix2D(eye_center, angle, scale)

        # Adjust translation so eye center lands at (desired_size * 0.5, desired_size * 0.38)
        t_x = (desired_size * 0.5) - eye_center[0]
        t_y = (desired_size * 0.38) - eye_center[1]
        M[0, 2] += t_x
        M[1, 2] += t_y

        aligned = cv2.warpAffine(
            img,
            M,
            (desired_size, desired_size),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(128, 128, 128),
        )
        return aligned

    def extract_embedding(
        self,
        image: np.ndarray,
        landmarks: Optional[np.ndarray] = None,
        crop_face: bool = True,
    ) -> List[float]:
        """
        Generates a unit-normalized 512-dimensional face embedding.

        Args:
          image: BGR image (original full image or face crop matching landmarks coordinate space)
          landmarks: 5 facial landmarks in image coordinate space (optional, used for affine rotation alignment)
          crop_face: whether to apply affine alignment

        Returns:
          List[float] of length 512, normalized so sum(x^2) == 1.0.
        """
        if image is None or image.size == 0:
            raise ValueError("Input image is empty or invalid.")

        # Step 1: Align face using facial landmarks if available
        if crop_face and landmarks is not None and len(landmarks) == 5:
            aligned_face = self.align_face(image, landmarks, desired_size=160)
        else:
            aligned_face = cv2.resize(image, (160, 160), interpolation=cv2.INTER_AREA)

        # Step 2: Convert BGR to RGB
        rgb_face = cv2.cvtColor(aligned_face, cv2.COLOR_BGR2RGB)

        # Step 3: Fixed standardization: (x - 127.5) / 128.0
        standardized = (rgb_face.astype(np.float32) - 127.5) / 128.0

        # Step 4: Convert to PyTorch tensor [1, 3, 160, 160]
        tensor = torch.from_numpy(standardized).permute(2, 0, 1).unsqueeze(0).to(self.device)

        # Step 5: Forward pass & L2 unit normalization
        with self._lock:
            with torch.no_grad():
                raw_emb = self._model(tensor)  # shape: (1, 512)
                l2_emb = raw_emb / torch.norm(raw_emb, p=2, dim=1, keepdim=True)

        vec = l2_emb.squeeze(0).cpu().numpy().tolist()

        # Sanity checks
        if len(vec) != 512:
            raise ValueError(f"Expected 512-dimensional embedding, got {len(vec)}")

        # Verify unit norm
        norm = sum(x * x for x in vec)
        if not (0.99 <= norm <= 1.01):
            raise ValueError(f"L2 normalization failed: norm = {norm}")

        return [round(float(x), 6) for x in vec]

    @staticmethod
    def compute_similarity(vec1: List[float], vec2: List[float]) -> float:
        """
        Computes cosine similarity between two unit-normalized vectors.
        For unit vectors, cosine similarity == dot product.
        Range: [-1.0, 1.0].

        Note:
          DEFAULT_SIMILARITY_THRESHOLD (0.70) is an engineering placeholder.
          Operational thresholds must be empirically calibrated via ROC/DET curves on
          held-out verification data resembling actual surveillance conditions.
        """
        if len(vec1) != len(vec2):
            raise ValueError(f"Vector dimensions mismatch ({len(vec1)} vs {len(vec2)})")
        return sum(a * b for a, b in zip(vec1, vec2))

    @staticmethod
    def compute_cosine_distance(vec1: List[float], vec2: List[float]) -> float:
        """
        Computes cosine distance (matches PostgreSQL <=> operator):
        distance = 1.0 - cosine_similarity.
        Range: [0.0, 2.0].

        Note:
          DEFAULT_COSINE_DISTANCE_THRESHOLD (0.30) is an engineering placeholder.
          The operational distance cutoff must be calibrated against validation curves.
        """
        sim = FaceEmbeddingEngine.compute_similarity(vec1, vec2)
        return max(0.0, 1.0 - sim)
