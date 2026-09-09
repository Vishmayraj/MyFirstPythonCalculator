"""
Face quality verification and pre-checks for Sentinel Person Watchlist.

Validates image integrity, payload security, face count, boundary completeness,
illumination, face ROI sharpness, and 3D head pose before embedding generation.
"""

import hashlib
import io
import math
import os
import threading
import urllib.request
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import cv2
import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger("sentinel.face.quality")

# Security limits
MAX_FILE_BYTES = 15 * 1024 * 1024  # 15 MB
MAX_DIMENSION = 4096                # 4096 px
MAX_PIXELS = 16 * 1024 * 1024       # 16 MP

# Verified YuNet Model Config
DEFAULT_WEIGHTS_DIR = Path(__file__).resolve().parents[2] / "weights"
YUNET_MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

# Canonical 3D facial feature points (in mm) for 5-point face model
# Landmarks: 0: right eye pupil, 1: left eye pupil, 2: nose tip, 3: right mouth corner, 4: left mouth corner
CANONICAL_3D_LANDMARKS = np.array([
    [-33.0, -32.0, -10.0],  # Right eye (up in image frame)
    [ 33.0, -32.0, -10.0],  # Left eye (up in image frame)
    [  0.0,   0.0,  32.0],  # Nose tip
    [-25.0,  35.0,  -5.0],  # Right mouth corner (down in image frame)
    [ 25.0,  35.0,  -5.0],  # Left mouth corner (down in image frame)
], dtype=np.float64)


class ModelIntegrityError(RuntimeError):
    """Raised when neural network weights fail cryptographic hash verification."""
    pass


@dataclass
class FaceQualityResult:
    """Detailed diagnostic outcome of the face quality evaluation."""
    passed: bool
    rejection_reason: Optional[str] = None
    face_bbox: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
    landmarks: Optional[np.ndarray] = None                  # (5, 2) array
    face_crop: Optional[np.ndarray] = None                  # BGR cropped face image
    full_image: Optional[np.ndarray] = None                 # BGR oriented original full image
    sharpness_face: float = 0.0
    sharpness_global: float = 0.0
    brightness_mean: float = 0.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)


class FaceQualityChecker:
    """
    Enterprise-grade face gatekeeper enforcing integrity, clarity, completeness, and 3D pose.
    """

    def __init__(
        self,
        weights_dir: Optional[Path] = None,
        min_sharpness_face: float = 30.0,
        min_face_size: int = 100,
        max_yaw_deg: float = 25.0,
        max_pitch_deg: float = 20.0,
        max_roll_deg: float = 20.0,
        min_brightness: float = 40.0,
        max_brightness: float = 220.0,
    ):
        self.weights_dir = Path(weights_dir) if weights_dir else DEFAULT_WEIGHTS_DIR
        self.weights_dir.mkdir(parents=True, exist_ok=True)
        self.yunet_path = self.weights_dir / YUNET_MODEL_NAME

        self.min_sharpness_face = min_sharpness_face
        self.min_face_size = min_face_size
        self.max_yaw_deg = max_yaw_deg
        self.max_pitch_deg = max_pitch_deg
        self.max_roll_deg = max_roll_deg
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness

        self._detector_lock = threading.Lock()
        self._detector: Optional[cv2.FaceDetectorYN] = None
        self._initialize_detector()

    def _initialize_detector(self) -> None:
        """Ensure weights exist, verify SHA-256 integrity, and initialize FaceDetectorYN."""
        # Check existing file integrity; if corrupted, delete so it can be re-fetched cleanly
        if self.yunet_path.exists():
            content = self.yunet_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != YUNET_SHA256:
                logger.warning("Existing YuNet weights failed SHA-256 check. Removing corrupted file.")
                self.yunet_path.unlink(missing_ok=True)

        # Download to a temporary file first if missing
        if not self.yunet_path.exists():
            tmp_path = self.yunet_path.with_suffix(".onnx.tmp")
            try:
                logger.info(f"Downloading YuNet face detection weights to {self.yunet_path}...")
                urllib.request.urlretrieve(YUNET_MODEL_URL, str(tmp_path))

                content = tmp_path.read_bytes()
                actual_hash = hashlib.sha256(content).hexdigest()
                if actual_hash != YUNET_SHA256:
                    tmp_path.unlink(missing_ok=True)
                    raise ModelIntegrityError(
                        f"YuNet model integrity check failed! Expected SHA256 {YUNET_SHA256}, "
                        f"got {actual_hash}. Refusing to load unverified neural network weights."
                    )

                # Atomically promote verified file
                tmp_path.replace(self.yunet_path)
                logger.info("YuNet verified and loaded with active SHA-256 integrity validation.")
            except Exception as e:
                tmp_path.unlink(missing_ok=True)
                if isinstance(e, ModelIntegrityError):
                    raise
                raise RuntimeError(f"Failed to download/verify YuNet weights: {e}") from e

        if hasattr(cv2, "FaceDetectorYN"):
            self._detector = cv2.FaceDetectorYN.create(
                model=str(self.yunet_path),
                config="",
                input_size=(320, 320),
                score_threshold=0.6,
                nms_threshold=0.3,
                top_k=5000,
            )
            logger.info("YuNet verified and loaded successfully.")
        else:
            raise RuntimeError("cv2.FaceDetectorYN is not available in the installed OpenCV library.")

    def evaluate(self, image_data: Any) -> FaceQualityResult:
        """
        Evaluate an image against all security, quality, completeness, and pose gates.
        """
        # ── Gate 1: Payload Security, EXIF Orientation, & Decoding ──
        img, decode_err = self._secure_load_and_orient(image_data)
        if img is None:
            return FaceQualityResult(passed=False, rejection_reason=decode_err)

        img_h, img_w = img.shape[:2]

        # Compute global sharpness for diagnostics/metrics (not gated, to support bokeh/portrait mode)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        global_sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # ── Gate 2: Face Detection & Single-Face Rule ───────────────
        detections = self._detect_faces_threadsafe(img)
        if len(detections) == 0:
            return FaceQualityResult(
                passed=False,
                sharpness_global=global_sharpness,
                rejection_reason="No human face detected. Watchlist photos must clearly feature a human face.",
            )

        if len(detections) > 1:
            return FaceQualityResult(
                passed=False,
                sharpness_global=global_sharpness,
                rejection_reason=(
                    f"Multiple faces detected ({len(detections)}). Watchlist registration "
                    "requires an individual portrait with exactly one person."
                ),
            )

        face = detections[0]
        rx, ry, rw, rh = face["bbox"]  # Raw detector bounding box
        landmarks = face["landmarks"]  # (5, 2) array

        # ── Gate 3: Boundary Completeness & Anti-Truncation Gate ────
        # Check A: Do any landmarks lie outside or dangerously close to image borders?
        border_pad = 10  # pixels
        for i, (lx, ly) in enumerate(landmarks):
            if lx < border_pad or lx > (img_w - border_pad) or ly < border_pad or ly > (img_h - border_pad):
                return FaceQualityResult(
                    passed=False,
                    face_bbox=(rx, ry, rw, rh),
                    landmarks=landmarks,
                    sharpness_global=global_sharpness,
                    rejection_reason=(
                        "Face is cut off or truncated at the image border (facial features fall "
                        "outside the visible frame). Please provide a portrait where the entire face is visible."
                    ),
                )

        # Check B: Does the raw bounding box extend significantly outside canvas?
        x1_clamp = max(0, rx)
        y1_clamp = max(0, ry)
        x2_clamp = min(img_w, rx + rw)
        y2_clamp = min(img_h, ry + rh)

        clamped_w = x2_clamp - x1_clamp
        clamped_h = y2_clamp - y1_clamp
        raw_area = max(1, rw * rh)
        clamped_area = max(1, clamped_w * clamped_h)

        if (clamped_area / raw_area) < 0.90 or rx <= 5 or (rx + rw >= img_w - 5):
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                landmarks=landmarks,
                sharpness_global=global_sharpness,
                rejection_reason=(
                    "Face is partially cut off at the edge of the photo. Watchlist registration "
                    "requires the subject's full, unclipped face."
                ),
            )

        # ── Gate 5: Usable Resolution & Illumination Check ──────────
        # Check actual usable clamped dimensions, not raw off-frame width/height
        if clamped_w < self.min_face_size or clamped_h < self.min_face_size:
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                sharpness_global=global_sharpness,
                rejection_reason=(
                    f"Face resolution too low ({clamped_w}x{clamped_h} px usable, minimum "
                    f"required is {self.min_face_size}x{self.min_face_size} px)."
                ),
            )

        face_crop = img[y1_clamp:y2_clamp, x1_clamp:x2_clamp]
        face_gray = gray[y1_clamp:y2_clamp, x1_clamp:x2_clamp]

        brightness_mean = float(np.mean(face_gray))
        if brightness_mean < self.min_brightness:
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                brightness_mean=brightness_mean,
                sharpness_global=global_sharpness,
                rejection_reason=(
                    f"Face is severely underexposed/too dark (mean luminance: {brightness_mean:.1f}, "
                    f"minimum: {self.min_brightness:.1f})."
                ),
            )

        if brightness_mean > self.max_brightness:
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                brightness_mean=brightness_mean,
                sharpness_global=global_sharpness,
                rejection_reason=(
                    f"Face is severely overexposed/washed out (mean luminance: {brightness_mean:.1f}, "
                    f"maximum: {self.max_brightness:.1f})."
                ),
            )

        # ── Gate 6: Face ROI Sharpness ──────────────────────────────
        face_sharpness = float(cv2.Laplacian(face_gray, cv2.CV_64F).var())
        if face_sharpness < self.min_sharpness_face:
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                sharpness_face=face_sharpness,
                sharpness_global=global_sharpness,
                brightness_mean=brightness_mean,
                rejection_reason=(
                    f"Face details are too blurry (face sharpness score: {face_sharpness:.1f}, "
                    f"minimum required: {self.min_sharpness_face:.1f}). Please provide a sharper photo."
                ),
            )

        # ── Gate 7: True 3D Head Pose Estimation (solvePnP) ─────────
        yaw_deg, pitch_deg, roll_deg, pose_err = self._estimate_head_pose(landmarks, (img_w, img_h))
        if pose_err:
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                rejection_reason=f"3D Head pose computation failed: {pose_err}",
            )

        if abs(yaw_deg) > self.max_yaw_deg:
            direction = "right" if yaw_deg > 0 else "left"
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                landmarks=landmarks,
                sharpness_face=face_sharpness,
                sharpness_global=global_sharpness,
                brightness_mean=brightness_mean,
                yaw_deg=yaw_deg,
                pitch_deg=pitch_deg,
                roll_deg=roll_deg,
                rejection_reason=(
                    f"Face is turned to the {direction} (yaw angle: {abs(yaw_deg):.1f}°, "
                    f"max allowed: {self.max_yaw_deg:.1f}°). Please upload a front-facing portrait."
                ),
            )

        if abs(pitch_deg) > self.max_pitch_deg:
            direction = "up" if pitch_deg > 0 else "down"
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                landmarks=landmarks,
                sharpness_face=face_sharpness,
                sharpness_global=global_sharpness,
                brightness_mean=brightness_mean,
                yaw_deg=yaw_deg,
                pitch_deg=pitch_deg,
                roll_deg=roll_deg,
                rejection_reason=(
                    f"Face is tilted {direction} (pitch angle: {abs(pitch_deg):.1f}°, "
                    f"max allowed: {self.max_pitch_deg:.1f}°). Please keep head level."
                ),
            )

        if abs(roll_deg) > self.max_roll_deg:
            return FaceQualityResult(
                passed=False,
                face_bbox=(rx, ry, rw, rh),
                face_crop=face_crop,
                landmarks=landmarks,
                sharpness_face=face_sharpness,
                sharpness_global=global_sharpness,
                brightness_mean=brightness_mean,
                yaw_deg=yaw_deg,
                pitch_deg=pitch_deg,
                roll_deg=roll_deg,
                rejection_reason=(
                    f"Face is tilted sideways (roll angle: {abs(roll_deg):.1f}°, "
                    f"max allowed: {self.max_roll_deg:.1f}°). Please keep head upright."
                ),
            )

        # ── All Gates Passed! ───────────────────────────────────────
        metrics = {
            "face_sharpness": round(face_sharpness, 1),
            "global_sharpness": round(global_sharpness, 1),
            "face_width_px": clamped_w,
            "face_height_px": clamped_h,
            "mean_luminance": round(brightness_mean, 1),
            "yaw_deg": round(yaw_deg, 1),
            "pitch_deg": round(pitch_deg, 1),
            "roll_deg": round(roll_deg, 1),
            "is_frontal": True,
            "detector_backend": "yunet_sha256_verified",
        }

        return FaceQualityResult(
            passed=True,
            face_bbox=(x1_clamp, y1_clamp, clamped_w, clamped_h),
            landmarks=landmarks,
            face_crop=face_crop,
            full_image=img,
            sharpness_face=face_sharpness,
            sharpness_global=global_sharpness,
            brightness_mean=brightness_mean,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            roll_deg=roll_deg,
            metrics=metrics,
        )

    def _secure_load_and_orient(self, data: Any) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """
        Safely load, enforce decompression-bomb limits, and auto-orient EXIF metadata.
        """
        try:
            if isinstance(data, (bytes, bytearray)):
                if len(data) > MAX_FILE_BYTES:
                    return None, f"Payload size ({len(data) / (1024*1024):.1f} MB) exceeds maximum allowed (15 MB)."
                pil_img = Image.open(io.BytesIO(data))
            elif isinstance(data, (str, Path)):
                file_path = Path(data)
                if file_path.stat().st_size > MAX_FILE_BYTES:
                    return None, "File size exceeds 15 MB limit."
                pil_img = Image.open(file_path)
            elif isinstance(data, np.ndarray):
                return data, None
            else:
                return None, "Unsupported image data format."

            # Guard against decompression bomb
            w, h = pil_img.size
            if w > MAX_DIMENSION or h > MAX_DIMENSION or (w * h) > MAX_PIXELS:
                return None, f"Image dimensions ({w}x{h}) exceed security limit (max 4096px / 16 Megapixels)."

            if w < 100 or h < 100:
                return None, f"Image dimensions too small ({w}x{h} px). Minimum required is 100x100 px."

            # Correct smartphone orientation via EXIF
            pil_img = ImageOps.exif_transpose(pil_img)
            rgb_arr = np.array(pil_img.convert("RGB"))
            bgr_arr = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
            return bgr_arr, None

        except Exception as e:
            return None, f"Image decoding security check failed: {str(e)}"

    def _detect_faces_threadsafe(self, img: np.ndarray) -> List[Dict[str, Any]]:
        """Thread-safe invocation of OpenCV YuNet face detector."""
        h, w = img.shape[:2]
        with self._detector_lock:
            if self._detector is None:
                raise RuntimeError("Face detector is not initialized.")
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(img)

        results = []
        if faces is not None:
            for face in faces:
                fx, fy, fw, fh = map(int, face[:4])
                # 5 landmarks in YuNet: [x0, y0, x1, y1, x2, y2, x3, y3, x4, y4]
                landmarks = face[4:14].reshape((5, 2))
                score = float(face[14])
                if score >= 0.6:
                    results.append({
                        "bbox": (fx, fy, fw, fh),
                        "landmarks": landmarks,
                        "score": score,
                    })
        return results

    def _estimate_head_pose(
        self,
        landmarks: np.ndarray,
        img_size: Tuple[int, int],
    ) -> Tuple[float, float, float, Optional[str]]:
        """
        Computes real 3D Euler angles (Yaw, Pitch, Roll) using solvePnP and canonical 3D model.
        """
        w, h = img_size
        image_points = landmarks.astype(np.float64)

        # Approximate camera intrinsics
        focal_length = float(w)
        center_x = float(w) / 2.0
        center_y = float(h) / 2.0
        camera_matrix = np.array([
            [focal_length, 0.0, center_x],
            [0.0, focal_length, center_y],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)

        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        # Use SQPNP (Sequential Quadratic Programming) which supports >= 4 points
        flag = getattr(cv2, "SOLVEPNP_SQPNP", cv2.SOLVEPNP_EPNP)
        success, rvec, _ = cv2.solvePnP(
            CANONICAL_3D_LANDMARKS,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=flag,
        )
        if not success:
            return 0.0, 0.0, 0.0, "solvePnP convergence failed"

        # Convert rotation vector to matrix
        R, _ = cv2.Rodrigues(rvec)

        # Decompose rotation matrix into Euler angles via RQ decomposition
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(R)
        pitch = float(angles[0])
        yaw = float(angles[1])
        roll = float(angles[2])

        return yaw, pitch, roll, None


class SecurityError(Exception):
    """Raised when model integrity or cryptographic checks fail."""
    pass
