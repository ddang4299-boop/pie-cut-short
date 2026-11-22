"""Step 3 - Detect content type: talking_head | screen_recording | podcast | gameplay_facecam.

Samples ~30 frame pairs (frame t and t + 0.2 s) from the proxy and measures:
- face_ratio        share of samples with at least one face
- multi_face_ratio  share of samples with two or more faces
- face_area         median area (fraction of frame) of the largest face, over samples with a face
- motion            mean absolute difference between the frames of a pair (0..1)
- static_ratio      share of pixels that did not change inside a pair (screen recordings ~ 1)
- speech_ratio      share of the duration covered by transcript words

`classify()` is a small rule set over those numbers (pure, unit-tested). The user can always
override the result in the UI; it only drives defaults (reframe mode, LLM hints).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

SAMPLES = 30
PAIR_GAP = 0.2  # seconds between the two frames of a pair
ANALYSIS_WIDTH = 320


def speech_ratio(words: Sequence[dict[str, Any]], duration: float) -> float:
    """Share of the duration covered by words (overlaps merged, gaps < 0.3 s count as speech)."""
    if not words or duration <= 0:
        return 0.0
    total, cur_s, cur_e = 0.0, None, None
    for w in sorted(words, key=lambda w: w["start"]):
        s, e = float(w["start"]), float(w["end"])
        if cur_e is not None and s - cur_e < 0.3:
            cur_e = max(cur_e, e)
            continue
        if cur_s is not None and cur_e is not None:
            total += cur_e - cur_s
        cur_s, cur_e = s, e
    if cur_s is not None and cur_e is not None:
        total += cur_e - cur_s
    return round(min(1.0, total / duration), 3)


def classify(m: dict[str, float]) -> tuple[str, float]:
    """Return (content_type, confidence 0..1) from the metrics described in the module doc."""
    face, multi, area = m.get("face_ratio", 0.0), m.get("multi_face_ratio", 0.0), m.get("face_area", 0.0)
    motion, static, speech = m.get("motion", 0.0), m.get("static_ratio", 0.0), m.get("speech_ratio", 0.0)

    if multi >= 0.35 and speech >= 0.4:
        return "podcast", min(1.0, 0.5 + multi / 2)
    if face >= 0.5 and area >= 0.02:
        if multi >= 0.2 and speech >= 0.4:
            return "podcast", 0.55
        return "talking_head", min(1.0, 0.5 + face / 2)
    if face >= 0.3 and area < 0.02:  # a small face in a corner: webcam over gameplay or a screen
        if motion >= 0.04 or static < 0.85:
            return "gameplay_facecam", 0.6
        return "screen_recording", 0.55
    if static >= 0.9 and motion < 0.03:
        return "screen_recording", min(1.0, 0.5 + static / 2)
    if motion >= 0.06:
        return "gameplay_facecam", 0.45
    if face >= 0.2:
        return "talking_head", 0.4
    return "screen_recording" if static >= 0.75 else "talking_head", 0.35


def _sample_times(duration: float, n: int) -> list[float]:
    if duration <= PAIR_GAP * 2:
        return [0.0]
    usable = duration - PAIR_GAP - 0.1
    step = usable / (n + 1)
    return [round(step * (i + 1), 3) for i in range(n)]


def _read_at(cap: Any, t: float) -> np.ndarray | None:
    import cv2

    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    return frame if ok else None


def measure(
    video: Path,
    *,
    duration: float,
    words: Sequence[dict[str, Any]] = (),
    models_dir: Path,
    samples: int = SAMPLES,
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, float]:
    import cv2

    from pipeline.faces import FaceDetector

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video}")
    counts, multi, areas, motions, statics = 0, 0, [], [], []
    used = 0
    backend = "none"
    try:
        with FaceDetector(models_dir) as detector:
            backend = detector.backend
            times = _sample_times(duration, samples)
            for k, t in enumerate(times):
                a = _read_at(cap, t)
                b = _read_at(cap, t + PAIR_GAP)
                if on_progress:
                    on_progress((k + 1) / len(times))
                if a is None:
                    continue
                used += 1
                h, w = a.shape[:2]
                scale = ANALYSIS_WIDTH / w
                small_a = cv2.resize(a, (ANALYSIS_WIDTH, max(1, int(h * scale))))
                faces = detector.detect(a)
                if faces:
                    counts += 1
                    areas.append(max(f[2] * f[3] for f in faces))
                    if len(faces) >= 2:
                        multi += 1
                if b is not None:
                    small_b = cv2.resize(b, (small_a.shape[1], small_a.shape[0]))
                    ga = cv2.cvtColor(small_a, cv2.COLOR_BGR2GRAY).astype(np.int16)
                    gb = cv2.cvtColor(small_b, cv2.COLOR_BGR2GRAY).astype(np.int16)
                    diff = np.abs(ga - gb)
                    motions.append(float(diff.mean()) / 255.0)
                    statics.append(float((diff <= 4).mean()))
    finally:
        cap.release()
    n = max(1, used)
    return {
        "face_ratio": round(counts / n, 3),
        "multi_face_ratio": round(multi / n, 3),
        "face_area": round(float(np.median(areas)), 4) if areas else 0.0,
        "motion": round(float(np.mean(motions)), 4) if motions else 0.0,
        "static_ratio": round(float(np.mean(statics)), 3) if statics else 0.0,
        "speech_ratio": speech_ratio(words, duration),
        "samples": used,
        "face_backend": backend,  # type: ignore[dict-item]
    }


def detect(video: Path, *, duration: float, words: Sequence[dict[str, Any]], models_dir: Path,
           on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    metrics = measure(video, duration=duration, words=words, models_dir=models_dir, on_progress=on_progress)
    kind, confidence = classify(metrics)
    return {"content_type": kind, "confidence": round(confidence, 2), "metrics": metrics}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="Detect the content type of a video.")
    p.add_argument("video", type=Path)
    p.add_argument("--transcript", type=Path)
    p.add_argument("--models-dir", type=Path, default=Path("../data/models"))
    args = p.parse_args(argv)
    import cv2

    cap = cv2.VideoCapture(str(args.video))
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, cap.get(cv2.CAP_PROP_FPS))
    cap.release()
    words = json.loads(args.transcript.read_text("utf-8"))["words"] if args.transcript else []
    print(json.dumps(detect(args.video, duration=duration, words=words, models_dir=args.models_dir), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
