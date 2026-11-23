"""Frame compositor for the FFmpeg render path: places source frames into the 9:16 output exactly
like the Remotion composition does (remotion/src/layers/VideoLayer.tsx) — fill (keyframed crop),
blur (blurred cover background + fitted foreground) and split (two source regions stacked).

`layout()` is pure geometry (unit tested against the TypeScript formulas); `compose()` applies it
to a BGR numpy frame with OpenCV. `frame_times()` maps output frames to source times with the same
rounding as Remotion's <Sequence> / trimBefore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from pipeline.keyframes import crop_rect, interpolate

Rect = tuple[float, float, float, float]  # left, top, width, height (pixels)


@dataclass(frozen=True)
class Placement:
    src: Rect  # source rectangle (source px)
    dst: Rect  # where it lands on the canvas (output px); the content is clipped to `clip`
    clip: tuple[int, int, int, int] | None = None  # x, y, w, h (output px) or None = whole canvas
    blur: bool = False  # cover background: blurred + darkened


def cover_rect(region: list[float], aspect: float, w: float, h: float) -> Rect:
    """Sub-rectangle (source px) of a normalized region with the panel's aspect, centered."""
    rx, ry, rw, rh = region[0] * w, region[1] * h, region[2] * w, region[3] * h
    if rw / rh > aspect:
        nw = rh * aspect
        return (rx + (rw - nw) / 2, ry, nw, rh)
    nh = rw / aspect
    return (rx, ry + (rh - nh) / 2, rw, nh)


def layout(edl: dict[str, Any], src_w: int, src_h: int, clip_t: float) -> list[Placement]:
    out = edl.get("output") or {"w": 1080, "h": 1920}
    W, H = int(out["w"]), int(out["h"])
    crop = edl.get("crop") or {}
    split = crop.get("split")
    if crop.get("mode") == "split" and split:
        top_h = int(round(H * float(split.get("ratio", 0.4))))
        top = cover_rect(split["top"], W / top_h, src_w, src_h)
        bottom = cover_rect(split["bottom"], W / (H - top_h), src_w, src_h)
        return [Placement(top, (0, 0, W, top_h), (0, 0, W, top_h)),
                Placement(bottom, (0, top_h, W, H - top_h), (0, top_h, W, H - top_h))]
    full = (0.0, 0.0, float(src_w), float(src_h))
    if crop.get("fit") == "blur":
        cover = max(W / src_w, H / src_h) * 1.08
        fit = min(W / src_w, H / src_h)
        bg = ((W - src_w * cover) / 2, (H - src_h * cover) / 2, src_w * cover, src_h * cover)
        fg = ((W - src_w * fit) / 2, (H - src_h * fit) / 2, src_w * fit, src_h * fit)
        return [Placement(full, bg, None, blur=True), Placement(full, fg)]
    x, y, zoom = interpolate(crop.get("keyframes") or [], clip_t, crop.get("easing", "linear"))
    l, t, w, h = crop_rect(x, y, zoom, src_w, src_h, W / H)
    return [Placement((l * src_w, t * src_h, w * src_w, h * src_h), (0, 0, W, H))]


def blur_sigma(out_w: int) -> float:
    """CSS blur(Npx) is a Gaussian with standard deviation N (VideoLayer uses 45 px at 1080 wide)."""
    return round(45 * out_w / 1080)


def _matrix(frame: np.ndarray, src: Rect, dst: Rect, origin: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """(image, affine matrix) mapping source rect `src` onto `dst` (relative to `origin`).
    When downscaling, the frame is first shrunk with area averaging to avoid aliasing."""
    import cv2

    sl, st, sw, sh = src
    dl, dt, dw, dh = dst
    sx, sy = dw / sw, dh / sh
    img = frame
    if min(sx, sy) < 0.75:
        f = max(sx, sy)
        img = cv2.resize(frame, (max(1, round(frame.shape[1] * f)), max(1, round(frame.shape[0] * f))),
                         interpolation=cv2.INTER_AREA)
        kx, ky = img.shape[1] / frame.shape[1], img.shape[0] / frame.shape[0]
        sl, st, sw, sh = sl * kx, st * ky, sw * kx, sh * ky
        sx, sy = dw / sw, dh / sh
    m = np.array([[sx, 0, dl - origin[0] - sl * sx], [0, sy, dt - origin[1] - st * sy]], dtype=np.float64)
    return img, m


BLUR_SCALE = 4  # the blurred background is computed at 1/4 size (as in VideoLayer.tsx)


def compose(frame: np.ndarray, placements: list[Placement], out_w: int, out_h: int) -> np.ndarray:
    import cv2

    canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    for p in placements:
        cx, cy, cw, ch = p.clip if p.clip else (0, 0, out_w, out_h)
        target = canvas[cy:cy + ch, cx:cx + cw]
        if p.blur:
            q = BLUR_SCALE
            qw, qh = max(1, cw // q), max(1, ch // q)
            dl, dt, dw, dh = p.dst
            img, m = _matrix(frame, p.src, ((dl - cx) / q, (dt - cy) / q, dw / q, dh / q), (0.0, 0.0))
            small = cv2.warpAffine(img, m, (qw, qh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            small = cv2.GaussianBlur(small, (0, 0), sigmaX=max(0.5, blur_sigma(out_w) / q))
            small = cv2.convertScaleAbs(small, alpha=0.7)
            target[:] = cv2.resize(small, (cw, ch), interpolation=cv2.INTER_LINEAR)
        else:
            img, m = _matrix(frame, p.src, p.dst, (cx, cy))
            cv2.warpAffine(img, m, (cw, ch), dst=target, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
    return canvas


def draw_progress(canvas: np.ndarray, progress: float, color: str) -> None:
    """Thin progress bar along the bottom edge (same geometry as remotion ProgressBar)."""
    h_px, w_px = canvas.shape[:2]
    bar_h = max(4, round(12 * (w_px / 1080)))
    band = canvas[h_px - bar_h:]
    band[:] = (band.astype(np.float32) * 0.78 + 255 * 0.22).astype(np.uint8)
    filled = int(round(max(0.0, min(1.0, progress)) * w_px))
    c = color.lstrip("#")
    band[:, :filled] = (int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16))


@dataclass(frozen=True)
class SegmentFrames:
    index: int
    first_frame: int  # output frame index where the segment starts
    count: int  # number of output frames
    source_start: float  # source time of its first frame


def frame_plan(segments: list[dict[str, float]], fps: float) -> list[SegmentFrames]:
    """Output frames per segment with Remotion's rounding (Sequence from/duration, trimBefore)."""
    plan, offset = [], 0.0
    for i, seg in enumerate(segments):
        length = seg["end"] - seg["start"]
        first = int(round(offset * fps))
        last = int(round((offset + length) * fps))
        plan.append(SegmentFrames(i, first, max(1, last - first), round(seg["start"] * fps) / fps))
        offset += length
    return plan


def total_frames(segments: list[dict[str, float]], fps: float) -> int:
    from pipeline.keyframes import clip_duration

    return max(1, int(round(clip_duration(segments) * fps)))


def source_time_of_frame(segments: list[dict[str, float]], fps: float, frame: int) -> float:
    """Source time shown at an output frame (inverse of frame_plan)."""
    plan = frame_plan(segments, fps)
    for sf in plan:
        if frame < sf.first_frame + sf.count:
            return sf.source_start + max(0, frame - sf.first_frame) / fps
    last = plan[-1]
    return last.source_start + (last.count - 1) / fps
