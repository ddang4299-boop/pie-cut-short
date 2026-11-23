"""Step 8b - Reframe: subject tracking -> crop keyframes (EDL `crop`), by content type.

  talking_head      follow the main face
  podcast           follow the active speaker (face whose mouth region moves most), hard cut on switch
  gameplay_facecam  split layout: webcam region on top, gameplay below
  screen_recording  follow the mouse cursor (pipeline/cursor_track.py)

Fallback chain when there is no face: OpenCV HOG person detector -> centered crop.
A source that is already vertical keeps its framing (one centered keyframe, zoom 1).

Planning (`plan_faces`, `plan_speakers`, `plan_split`) is pure and unit-tested on synthetic
detections; `compute()` reads frames and is run in the vision child process.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from pipeline import keyframes as kf

SAMPLE_FPS = 5.0
ANALYSIS_WIDTH = 640
DEFAULT_SPLIT_RATIO = 0.4  # share of the output height used by the top (facecam) panel
MODE_BY_CONTENT = {
    "talking_head": "auto",
    "podcast": "auto",
    "gameplay_facecam": "split",
    "screen_recording": "cursor",
}

Box = tuple[float, float, float, float, float]  # normalized x, y (top-left), w, h, score
Sample = tuple[float, list[Box]]  # (clip time, detections)


def default_mode(content_type: str | None) -> str:
    return MODE_BY_CONTENT.get(content_type or "", "auto")


def _center(b: Sequence[float]) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def subject_point(b: Sequence[float]) -> tuple[float, float]:
    """Where the crop should be centered for a face box: a little below the face so the head
    sits in the upper part of the frame (matters only when zoomed in)."""
    cx, cy = _center(b)
    return cx, min(1.0, cy + b[3] * 0.6)


# --------------------------------------------------------------------------
# tracking (pure)
# --------------------------------------------------------------------------
class Tracks:
    """Greedy nearest-center association of detections across samples."""

    def __init__(self, max_jump: float = 0.15, max_missing: float = 1.5):
        self.max_jump, self.max_missing = max_jump, max_missing
        self.items: list[dict[str, Any]] = []
        self._next = 0

    def update(self, t: float, boxes: Sequence[Box]) -> list[tuple[int, Box, int]]:
        """Return [(track_id, box, index of the box in `boxes`)] for this sample."""
        self.items = [tr for tr in self.items if t - tr["last"] <= self.max_missing]
        free = list(self.items)
        out: list[tuple[int, Box, int]] = []
        for idx in sorted(range(len(boxes)), key=lambda i: -boxes[i][2] * boxes[i][3]):
            b = boxes[idx]
            cx, cy = _center(b)
            best, best_d = None, self.max_jump + max(b[2], b[3])
            for tr in free:
                d = math.hypot(tr["cx"] - cx, tr["cy"] - cy)
                if d < best_d:
                    best, best_d = tr, d
            if best is None:
                best = {"id": self._next, "seen": 0}
                self._next += 1
                self.items.append(best)
            else:
                free.remove(best)
            best.update(cx=cx, cy=cy, box=b, last=t, seen=best["seen"] + 1)
            out.append((best["id"], b, idx))
        return out


def main_subject_track(samples: Sequence[Sample]) -> list[tuple[float, Box | None]]:
    """For each sample: the box of the main subject (largest, steadiest face) or None."""
    tracks = Tracks()
    per_sample = [tracks.update(t, boxes) for t, boxes in samples]
    weight: dict[int, float] = {}
    for assoc in per_sample:
        for tid, b, _ in assoc:
            weight[tid] = weight.get(tid, 0.0) + b[2] * b[3] * max(0.3, b[4])
    out: list[tuple[float, Box | None]] = []
    current: int | None = None
    for (t, _), assoc in zip(samples, per_sample):
        boxes = {tid: b for tid, b, _ in assoc}
        if current not in boxes:
            # the current subject is gone: switch to the heaviest one visible
            current = max(boxes, key=lambda tid: weight[tid]) if boxes else current
        out.append((t, boxes.get(current) if current is not None else None))
    return out


def fill_gaps(points: Sequence[tuple[float, tuple[float, float] | None]],
              default: tuple[float, float] = (0.5, 0.5)) -> tuple[list[float], list[float], list[float], float]:
    """Hold the last known position while the subject is lost (the first known one before it is
    seen). Returns times, xs, ys and the share of samples that had a detection."""
    known = [p for _, p in points if p is not None]
    if not known:
        return [t for t, _ in points], [default[0]] * len(points), [default[1]] * len(points), 0.0
    last = known[0]
    xs, ys = [], []
    for _, p in points:
        if p is not None:
            last = p
        xs.append(last[0])
        ys.append(last[1])
    return [t for t, _ in points], xs, ys, len(known) / len(points)


def plan_faces(samples: Sequence[Sample], *, src_w: float, src_h: float, out_aspect: float,
               zoom: float = 1.0) -> dict[str, Any]:
    """Follow the main face. Returns {keyframes, found_ratio}."""
    track = main_subject_track(samples)
    times, xs, ys, found = fill_gaps([(t, subject_point(b) if b else None) for t, b in track])
    keyframes = kf.smooth_track(times, xs, ys, zoom=zoom, src_w=src_w, src_h=src_h, out_aspect=out_aspect)
    return {"keyframes": keyframes, "found_ratio": round(found, 3)}


def plan_speakers(samples: Sequence[Sample], activity: Sequence[dict[int, float]] | None = None, *,
                  src_w: float, src_h: float, out_aspect: float, zoom: float = 1.0,
                  switch_ratio: float = 1.5, min_hold: float = 1.5) -> dict[str, Any]:
    """Podcast: follow the active speaker.

    `activity[i]` maps the index of a box in samples[i] to its mouth-motion score. A switch needs
    the other face to be `switch_ratio`x more active (smoothed) and the current shot to have lasted
    `min_hold` seconds; a switch is a hard cut (two keyframes 10 ms apart), never a long pan.
    """
    tracks = Tracks()
    level: dict[int, float] = {}
    speaker: int | None = None
    since = -1e9
    shots: list[tuple[int | None, list[tuple[float, tuple[float, float] | None]]]] = []
    for i, (t, boxes) in enumerate(samples):
        assoc = tracks.update(t, boxes)
        acts = activity[i] if activity and i < len(activity) else {}
        for tid, _, k in assoc:
            level[tid] = 0.6 * level.get(tid, 0.0) + 0.4 * float(acts.get(k, 0.0))
        visible = {tid: b for tid, b, _ in assoc}
        if visible:
            best = max(visible, key=lambda tid: (level.get(tid, 0.0), visible[tid][2] * visible[tid][3]))
            if speaker not in visible:
                speaker, since = best, t
            elif best != speaker and t - since >= min_hold and \
                    level.get(best, 0.0) > switch_ratio * max(1e-6, level.get(speaker, 0.0)):
                speaker, since = best, t
        if not shots or shots[-1][0] != speaker:
            shots.append((speaker, []))
        box = visible.get(speaker) if speaker is not None else None
        shots[-1][1].append((t, subject_point(box) if box else None))

    keyframes: list[kf.Keyframe] = []
    found_n, total = 0, 0
    last_point = (0.5, 0.5)
    for _, points in shots:
        times, xs, ys, found = fill_gaps(points, default=last_point)
        found_n += round(found * len(points))
        total += len(points)
        shot_kfs = kf.smooth_track(times, xs, ys, zoom=zoom, src_w=src_w, src_h=src_h, out_aspect=out_aspect)
        if keyframes and shot_kfs:
            # hard cut: hold the previous shot until just before this one starts
            prev = dict(keyframes[-1])
            prev["t"] = round(max(prev["t"], shot_kfs[0]["t"] - 0.01), 3)
            if prev["t"] > keyframes[-1]["t"]:
                keyframes.append(prev)
        keyframes += shot_kfs
        last_point = (xs[-1], ys[-1]) if xs else last_point
    return {"keyframes": keyframes, "found_ratio": round(found_n / total, 3) if total else 0.0,
            "shots": len(shots)}


def fit_region(cx: float, cy: float, w: float, aspect: float, src_w: float, src_h: float) -> list[float]:
    """Normalized [x, y, w, h] of a region centered on (cx, cy), `w` wide (normalized), with pixel
    aspect `aspect` (w/h), shrunk to fit and shifted inside the frame."""
    pw = w * src_w
    ph = pw / aspect
    if ph > src_h:
        ph, pw = src_h, src_h * aspect
    if pw > src_w:
        pw, ph = src_w, src_w / aspect
    x = min(max(cx * src_w - pw / 2, 0.0), src_w - pw)
    y = min(max(cy * src_h - ph / 2, 0.0), src_h - ph)
    return [round(x / src_w, 4), round(y / src_h, 4), round(pw / src_w, 4), round(ph / src_h, 4)]


def split_aspects(out_w: float, out_h: float, ratio: float) -> tuple[float, float]:
    """Pixel aspect (w/h) of the top and bottom panels of a split layout."""
    return out_w / (out_h * ratio), out_w / (out_h * (1 - ratio))


def plan_split(samples: Sequence[Sample], *, src_w: float, src_h: float, out_w: float, out_h: float,
               ratio: float = DEFAULT_SPLIT_RATIO, expand: float = 2.2) -> dict[str, Any]:
    """Gameplay + facecam: the webcam is a small, steady face. Top panel = the face box expanded
    `expand` times; bottom panel = the gameplay, centered on the rest of the frame."""
    top_aspect, bottom_aspect = split_aspects(out_w, out_h, ratio)
    boxes = [max(bs, key=lambda b: b[4]) for _, bs in samples if bs]
    found = len(boxes) / len(samples) if samples else 0.0
    if boxes:
        med = np.median(np.array([b[:4] for b in boxes]), axis=0)
        cx, cy = float(med[0] + med[2] / 2), float(med[1] + med[3] / 2)
        top = fit_region(cx, cy, float(med[2]) * expand, top_aspect, src_w, src_h)
        # gameplay: as wide as possible, centered away from the webcam horizontally
        bottom_cx = 0.5 if abs(cx - 0.5) < 0.15 else (0.5 + (0.5 - cx) * 0.3)
    else:
        top = fit_region(0.15, 0.2, 0.3, top_aspect, src_w, src_h)
        bottom_cx = 0.5
    bottom = fit_region(bottom_cx, 0.5, 1.0, bottom_aspect, src_w, src_h)
    return {"split": {"top": top, "bottom": bottom, "ratio": ratio}, "found_ratio": round(found, 3)}


# --------------------------------------------------------------------------
# frames (I/O)
# --------------------------------------------------------------------------
def iter_frames(video: Path, segments: Sequence[kf.Segment], fps: float = SAMPLE_FPS,
                width: int = ANALYSIS_WIDTH) -> Iterator[tuple[float, float, np.ndarray]]:
    """Yield (clip_t, source_t, bgr_small) at `fps` over the segments. Seeks once per segment and
    then reads sequentially (grab() skips the frames in between without decoding to BGR)."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video}")
    try:
        offset = 0.0
        for seg in segments:
            start, end = float(seg["start"]), float(seg["end"])
            cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
            next_t = start
            while True:
                if not cap.grab():
                    break
                pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                if pos > end + 1e-3:
                    break
                if pos + 1e-3 < next_t:
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    break
                h, w = frame.shape[:2]
                if w > width:
                    frame = cv2.resize(frame, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
                yield offset + max(0.0, pos - start), pos, frame
                next_t += 1.0 / fps
            offset += end - start
    finally:
        cap.release()


def _mouth_motion(prev: np.ndarray | None, cur: np.ndarray, box: Box) -> float:
    """Mean abs difference inside the lower half of a face box between two consecutive samples."""
    if prev is None:
        return 0.0
    h, w = cur.shape[:2]
    x0, y0 = int(box[0] * w), int((box[1] + box[3] * 0.5) * h)
    x1, y1 = int((box[0] + box[2]) * w), int((box[1] + box[3]) * h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    a = prev[y0:y1, x0:x1].astype(np.int16)
    b = cur[y0:y1, x0:x1].astype(np.int16)
    return float(np.abs(a - b).mean()) / 255.0


def detect_people(frame: np.ndarray, hog: Any) -> list[Box]:
    """OpenCV HOG pedestrian detector (fallback when no face is found)."""
    h, w = frame.shape[:2]
    rects, weights = hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
    out: list[Box] = []
    for (x, y, bw, bh), score in zip(rects, np.ravel(weights) if len(rects) else []):
        # use the upper part of the body (head and shoulders) as the subject box
        out.append((x / w, y / h, bw / w, bh * 0.4 / h, float(min(1.0, score))))
    return out


def collect(video: Path, segments: Sequence[kf.Segment], *, models_dir: Path, people_fallback: bool = True,
            on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Run face detection over the segments. Returns {samples, activity, people_samples, backend}."""
    import cv2

    from pipeline.faces import FaceDetector

    total = max(1, int(kf.clip_duration(segments) * SAMPLE_FPS))
    samples: list[Sample] = []
    activity: list[dict[int, float]] = []
    frames_for_people: list[tuple[float, np.ndarray]] = []
    prev_gray: np.ndarray | None = None
    with FaceDetector(models_dir) as detector:
        backend = detector.backend
        for n, (clip_t, _src_t, frame) in enumerate(iter_frames(video, segments)):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detect(frame)
            samples.append((round(clip_t, 3), faces))
            activity.append({i: _mouth_motion(prev_gray, gray, b) for i, b in enumerate(faces)})
            if people_fallback and n % 5 == 0:
                frames_for_people.append((round(clip_t, 3), frame))
            prev_gray = gray
            if on_progress:
                on_progress(min(0.95, (n + 1) / total))
    people: list[Sample] = []
    if people_fallback and not any(bs for _, bs in samples) and frames_for_people:
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        people = [(t, detect_people(f, hog)) for t, f in frames_for_people]
    return {"samples": samples, "activity": activity, "people_samples": people, "backend": backend}


def compute(video: Path, segments: Sequence[kf.Segment], *, content_type: str | None, mode: str | None,
            src_w: int, src_h: int, out_w: int = 1080, out_h: int = 1920, models_dir: Path,
            on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Crop for an EDL: {mode, keyframes, split?, fit, info}. `mode` None = default for the content type."""
    out_aspect = out_w / out_h
    mode = mode or default_mode(content_type)
    info: dict[str, Any] = {"content_type": content_type, "mode": mode, "sample_fps": SAMPLE_FPS}
    centered = [{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0}]
    if kf.is_vertical(src_w, src_h, out_aspect):
        info["method"] = "already_vertical"
        return {"mode": "manual", "keyframes": centered, "fit": "fill", "info": info}
    if mode == "cursor":
        from pipeline import cursor_track

        result = cursor_track.compute(video, segments, src_w=src_w, src_h=src_h, out_aspect=out_aspect,
                                      on_progress=on_progress)
        info.update(result["info"])
        return {"mode": "cursor", "keyframes": result["keyframes"], "fit": result["fit"], "info": info}

    data = collect(video, segments, models_dir=models_dir, on_progress=on_progress)
    samples = data["samples"]
    info["face_backend"] = data["backend"]
    if mode == "split":
        plan = plan_split(samples, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
        info.update(method="facecam_split", found_ratio=plan["found_ratio"])
        return {"mode": "split", "keyframes": centered, "split": plan["split"], "fit": "fill", "info": info}
    if any(bs for _, bs in samples):
        if content_type == "podcast":
            plan = plan_speakers(samples, data["activity"], src_w=src_w, src_h=src_h, out_aspect=out_aspect)
            info.update(method="active_speaker", shots=plan["shots"])
        else:
            plan = plan_faces(samples, src_w=src_w, src_h=src_h, out_aspect=out_aspect)
            info["method"] = "face"
        info["found_ratio"] = plan["found_ratio"]
        return {"mode": "auto", "keyframes": plan["keyframes"], "fit": "fill", "info": info}
    people = data["people_samples"]
    if any(bs for _, bs in people):
        plan = plan_faces(people, src_w=src_w, src_h=src_h, out_aspect=out_aspect)
        info.update(method="person", found_ratio=plan["found_ratio"])
        return {"mode": "auto", "keyframes": plan["keyframes"], "fit": "fill", "info": info}
    info.update(method="center", found_ratio=0.0)
    return {"mode": "auto", "keyframes": centered, "fit": "fill", "info": info}
