"""Face detection shared by content-type detection (Phase 2) and reframing (Phase 3).

MediaPipe Tasks FaceDetector (BlazeFace short-range). The model file (~230 KB) is downloaded once to
data/models/mediapipe/. If MediaPipe or the model is unavailable, OpenCV's bundled Haar cascade
is used instead (slower and less accurate, but offline).

Boxes are returned normalized to the frame: (x, y, w, h, score), x/y = top-left corner.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("shortforge.faces")

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)
MODEL_NAME = "blaze_face_short_range.tflite"

Box = tuple[float, float, float, float, float]


def ensure_model(models_dir: Path) -> Path | None:
    path = Path(models_dir) / "mediapipe" / MODEL_NAME
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=30) as resp, tmp.open("wb") as f:
            f.write(resp.read())
        tmp.replace(path)
        return path
    except OSError as exc:
        log.warning("could not download the MediaPipe face model: %s", exc)
        tmp.unlink(missing_ok=True)
        return None


class FaceDetector:
    """detect(bgr_frame) -> list[Box]. Use as a context manager or call close()."""

    def __init__(self, models_dir: Path, min_confidence: float = 0.5):
        self.backend = "none"
        self._mp: Any = None
        self._det: Any = None
        model = ensure_model(models_dir)
        if model is not None:
            try:
                import mediapipe as mp
                from mediapipe.tasks.python import BaseOptions, vision

                options = vision.FaceDetectorOptions(
                    base_options=BaseOptions(model_asset_path=str(model)),
                    min_detection_confidence=min_confidence,
                )
                self._det = vision.FaceDetector.create_from_options(options)
                self._mp = mp
                self.backend = "mediapipe"
            except Exception as exc:  # noqa: BLE001 - any import/runtime problem -> fallback
                log.warning("MediaPipe face detector unavailable (%s), using Haar cascade", exc)
        if self._det is None:
            import cv2

            cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
            if not cascade.empty():
                self._det = cascade
                self.backend = "haar"

    def detect(self, bgr: np.ndarray) -> list[Box]:
        if self._det is None:
            return []
        h, w = bgr.shape[:2]
        if self.backend == "mediapipe":
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
            result = self._det.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb))
            boxes: list[Box] = []
            for d in result.detections:
                bb = d.bounding_box
                score = d.categories[0].score if d.categories else 1.0
                boxes.append(_clip_box(bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h, score))
            return boxes
        import cv2

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        found = self._det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, w // 40),) * 2)
        return [_clip_box(x / w, y / h, bw / w, bh / h, 0.6) for (x, y, bw, bh) in found]

    def close(self) -> None:
        if self.backend == "mediapipe" and self._det is not None:
            self._det.close()
        self._det = None

    def __enter__(self) -> FaceDetector:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _clip_box(x: float, y: float, w: float, h: float, score: float) -> Box:
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(1.0, x + w), min(1.0, y + h)
    return (round(x0, 4), round(y0, 4), round(max(0.0, x1 - x0), 4), round(max(0.0, y1 - y0), 4), round(float(score), 3))
