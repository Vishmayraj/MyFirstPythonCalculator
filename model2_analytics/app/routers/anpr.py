#!/usr/bin/env python
"""ANPR Pipeline API Router
POST /api/v1/anpr/process - Process image through full ANPR pipeline
GET  /api/v1/anpr/health  - Health check
"""
import sys, os, time, uuid, tempfile
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/anpr", tags=["anpr"])
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

def _get_db():
    """Get a SQLAlchemy Session using the app's configured engine (works in Docker + local)."""
    import shared.db.session as _s
    return _s._SessionLocal() if _s._SessionLocal else None

def _broadcast_alert(payload):
    """Broadcast alert events to WebSocket clients via the detections router's hub.

    Reuses the *same* module instance main.py mounted (sys.modules key
    `model2.routers.detections`) so the shared ACTIVE_WS/_loop state is used.
    """
    try:
        import importlib.util as _ilu
        mod = sys.modules.get("model2.routers.detections")
        if mod is None:
            _spec = _ilu.spec_from_file_location(
                "model2.routers.detections",
                Path(__file__).resolve().parent / "detections.py",
            )
            mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(mod)
        details = payload.get("payload", payload)
        mod.on_detection_event({"type": "WATCHLIST_ALERT", "alert": details})
    except Exception as _e:
        import logging
        logging.getLogger("sentinel.anpr").warning(f"WS broadcast failed: {_e}")

@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "anpr-pipeline"}


@router.post("/process")
async def process_image(file: UploadFile = File(...), expected_plate: Optional[str] = Form(None)):
    """Process an image through the full ANPR pipeline + alert generation."""
    import cv2
    from pipeline.detection.vehicle_detector import VehicleDetector
    from pipeline.plate.plate_detector import PlateDetector
    from pipeline.ocr.onnx_ocr_engine import OnnxOCREngine
    from pipeline.events.watchlist_matcher import normalize_plate, WatchlistMatcher
    from pipeline.events.alert_service import AlertService

    suffix = Path(file.filename or ".jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        frame = cv2.imread(tmp_path)
        if frame is None:
            raise HTTPException(status_code=400, detail="Could not load image")
        H, W = frame.shape[:2]
        steps = {}
        t0 = time.time()

        vehicle_detector = VehicleDetector(confidence_threshold=0.10, iou_threshold=0.45)
        plate_detector = PlateDetector(confidence_threshold=0.20)
        ocr_engine = OnnxOCREngine(use_gpu=False)
        ocr_engine._lazy_load()
        steps["1"] = {"status": "success", "time": f"{time.time()-t0:.2f}s", "detail": "Models loaded"}

        t0 = time.time()
        vehicles = vehicle_detector.detect(frame)
        steps["2"] = {"status": "success", "time": f"{time.time()-t0:.2f}s", "detail": f"{len(vehicles)} vehicle(s)"}

        t0 = time.time()
        plate_dets = plate_detector.detect(frame)
        steps["3"] = {"status": "success" if plate_dets else "error", "time": f"{time.time()-t0:.2f}s", "detail": f"{len(plate_dets)} plate(s)"}

        if not plate_dets:
            return JSONResponse({"success": False, "steps": steps, "message": "No plates detected"})

        t0 = time.time()
        plate_results = []
        for p in plate_dets:
            px1, py1, px2, py2 = p.bbox
            pad = int(0.06 * max(px2-px1, py2-py1))
            crop = frame[max(0, py1-pad):min(H, py2+pad), max(0, px1-pad):min(W, px2+pad)]
            if crop.size == 0:
                continue
            ocr_result = ocr_engine.read_plate(crop)
            if ocr_result:
                plate_results.append({"text": ocr_result.plate_text, "confidence": ocr_result.confidence, "bbox": p.bbox, "det_conf": p.confidence})

        steps["4"] = {"status": "success" if plate_results else "error", "time": f"{time.time()-t0:.2f}s", "detail": f"{len(plate_results)} OCR result(s)"}

        if not plate_results:
            return JSONResponse({"success": False, "steps": steps, "message": "OCR failed"})

        best = max(plate_results, key=lambda x: x["confidence"])
        normalized = normalize_plate(best["text"])

        px1, py1, px2, py2 = best["bbox"]
        pad = int(0.06 * max(px2-px1, py2-py1))
        crop = frame[max(0, py1-pad):min(H, py2+pad), max(0, px1-pad):min(W, px2+pad)]
        crop_path = None
        if crop.size > 0:
            crop_dir = Path("/model2-analytics/detection-image")
            if not crop_dir.exists():
                crop_dir = Path(__file__).resolve().parents[3] / "model2-analytics" / "detection-image"
            crop_dir.mkdir(parents=True, exist_ok=True)
            crop_file = crop_dir / f"{uuid.uuid4()}.jpg"
            cv2.imwrite(str(crop_file), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            crop_path = f"/detection-image/{crop_file.name}"

        t0 = time.time()
        db = _get_db()
        is_watchlisted = False
        alert_id = None
        detection_id = None
        wl_category = None
        wl_description = None
        wl_severity = None

        if db:
            try:
                matcher = WatchlistMatcher()
                match = matcher.check_plate(db, normalized)
                is_watchlisted = match is not None

                if match:
                    wl_category = match.category
                    wl_description = match.description
                    wl_severity = match.severity

                    from sqlalchemy import text as _text
                    # detections.camera_id has an FK to cameras — resolve a real camera
                    cam_row = db.execute(_text("SELECT id FROM cameras ORDER BY name LIMIT 1")).first()
                    camera_id = str(cam_row[0]) if cam_row else "00000000-0000-0000-0000-000000000000"
                    detection_id = str(uuid.uuid4())
                    db.execute(
                        _text("INSERT INTO detections (id, \"timestamp\", detected_plate, confidence, cropped_image_path, camera_id) VALUES (:id, NOW(), :plate, :conf, :crop_path, :camera_id)"),
                        {"id": detection_id, "plate": normalized, "conf": float(best["confidence"]), "crop_path": crop_path, "camera_id": camera_id},
                    )
                    db.commit()

                    alert_service = AlertService(on_detection_event=_broadcast_alert)
                    alert_id = alert_service.create_alert(
                        db=db, detection_id=detection_id, match=match,
                        camera_name="Test Camera", plate_text=normalized,
                        vehicle_class=vehicles[0].class_name if vehicles else None,
                        crop_path=crop_path, confidence=float(best["confidence"]),
                    )
            except Exception as e:
                import logging
                logging.getLogger("sentinel.anpr").warning(f"DB operations failed: {e}")
                if db:
                    db.rollback()
            finally:
                if db:
                    db.close()

        steps["5"] = {"status": "success", "time": f"{time.time()-t0:.2f}s", "detail": "Watchlisted" if is_watchlisted else "Clean"}

        response = {
            "success": True,
            "steps": steps,
            "plate": {"text": best["text"], "normalized": normalized, "confidence": best["confidence"], "detection_confidence": best["det_conf"], "bbox": [int(c) for c in best["bbox"]]},
            "alert": {"is_watchlisted": is_watchlisted, "category": wl_category, "severity": wl_severity, "description": wl_description, "alert_id": alert_id, "detection_id": detection_id},
            "vehicles": [{"class": v.class_name, "confidence": v.confidence} for v in vehicles],
        }

        if expected_plate:
            response["expected_plate"] = expected_plate.upper()
            response["match"] = normalized == expected_plate.upper()

        return JSONResponse(response)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass
