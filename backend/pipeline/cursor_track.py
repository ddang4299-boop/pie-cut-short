"""Screen recordings: follow the mouse cursor.

The cursor is found by frame differencing: between two consecutive samples (10 fps, grayscale,
640 px wide) the pixels that changed are thresholded and cleaned with a small morphology; the
connected regions that are cursor-sized are candidates. Large changes (scrolling, window switches,
video playing in the recording) are ignored for that sample. The candidate closest to the last
known position wins; when nothing qualifies the position is held.

The track is smoothed like a face track (One-Euro -> dead zone -> speed cap -> few keyframes) with a
zoom of 1.3x so the area around the cursor is readable on a phone. If the cursor is found in less
than MIN_FOUND of the samples, the crop falls back to `fit: "blur"`: the whole screen centered over
a blurred, enlarged copy of itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from pipeline import keyframes as kf

SAMPLE_FPS = 10.0
ANALYSIS_WIDTH = 640
DIFF_THRESHOLD = 25  # 0..255
MAX_CHANGED = 0.20  # more than this share of pixels changed: scroll / scene change, skip
MIN_SIDE, MAX_SIDE = 2, 48  # cursor-sized region, in analysis pixels
MAX_AREA = 1400
MAX_CANDIDATES = 8  # more small regions than this: typing / animation noise, keep only the nearest
ZOOM = 1.3
MIN_FOUND = 0.2

Point = tuple[float, float]


def find_cursor(prev: np.ndarray, cur: np.ndarray, last: Point | None = None) -> Point | None:
    """Normalized (x, y) of the cursor-sized change between two grayscale frames, or None."""
    import cv2

    diff = cv2.absdiff(prev, cur)
    mask = (diff > DIFF_THRESHOLD).astype(np.uint8)
    changed = float(mask.mean())
    if changed == 0.0 or changed > MAX_CHANGED:
        return None
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.dilate(mask, kernel, iterations=2)
    n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = mask.shape
    candidates: list[Point] = []
    for i in range(1, n):
        bw, bh, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        if MIN_SIDE <= bw <= MAX_SIDE and MIN_SIDE <= bh <= MAX_SIDE and area <= MAX_AREA:
            cx, cy = centroids[i]
            candidates.append((float(cx) / w, float(cy) / h))
    if not candidates:
        return None
    if last is None:
        if len(candidates) > 2:
            return None  # ambiguous start: wait for a clean sample
        return candidates[0]
    best = min(candidates, key=lambda p: (p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2)
    if len(candidates) > MAX_CANDIDATES and (best[0] - last[0]) ** 2 + (best[1] - last[1]) ** 2 > 0.01:
        return None  # lots of noise and nothing near the cursor: hold
    return best


def track(frames: Iterable[tuple[float, np.ndarray]]) -> list[tuple[float, Point | None]]:
    """[(clip_t, gray frame)] -> [(clip_t, cursor point or None)]."""
    out: list[tuple[float, Point | None]] = []
    prev: np.ndarray | None = None
    last: Point | None = None
    for t, gray in frames:
        point = find_cursor(prev, gray, last) if prev is not None and prev.shape == gray.shape else None
        if point is not None:
            last = point
        out.append((t, point))
        prev = gray
    return out


def plan(points: Sequence[tuple[float, Point | None]], *, src_w: float, src_h: float, out_aspect: float,
         zoom: float = ZOOM) -> dict[str, Any]:
    from pipeline.reframe import fill_gaps

    times, xs, ys, found = fill_gaps(points)
    if found < MIN_FOUND:
        return {"keyframes": [{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0}], "fit": "blur",
                "info": {"method": "blur_fallback", "found_ratio": round(found, 3)}}
    keyframes = kf.smooth_track(times, xs, ys, zoom=zoom, src_w=src_w, src_h=src_h, out_aspect=out_aspect,
                                dead_x=0.06, dead_y=0.06, min_cutoff=0.5, beta=0.8)
    return {"keyframes": keyframes, "fit": "fill", "info": {"method": "cursor", "found_ratio": round(found, 3)}}


def compute(video: Path, segments: Sequence[kf.Segment], *, src_w: float, src_h: float, out_aspect: float,
            on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    import cv2

    from pipeline.reframe import iter_frames

    total = max(1, int(kf.clip_duration(segments) * SAMPLE_FPS))

    def frames() -> Iterable[tuple[float, np.ndarray]]:
        for n, (clip_t, _src_t, bgr) in enumerate(iter_frames(video, segments, fps=SAMPLE_FPS, width=ANALYSIS_WIDTH)):
            if on_progress:
                on_progress(min(0.95, (n + 1) / total))
            yield round(clip_t, 3), cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    return plan(track(frames()), src_w=src_w, src_h=src_h, out_aspect=out_aspect)
