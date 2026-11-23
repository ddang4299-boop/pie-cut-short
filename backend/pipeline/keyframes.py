"""Crop keyframes: geometry, interpolation, clip/source time mapping and smoothing (pure functions).

Conventions (shared with frontend/src/lib/edl.ts — keep both in sync, see tests):
- A keyframe is {t, x, y, zoom}. `t` is **clip time** (seconds from the start of the clip, i.e. on
  the concatenated segments). `x`, `y` are the crop **center** normalized to the source frame
  (0..1). `zoom` = 1 means the largest output-aspect rectangle that fits in the source; 2 = half
  that width and height.
- The crop is clamped so it never leaves the frame.
- Between keyframes the values are interpolated linearly or with ease-in-out (smoothstep);
  before the first / after the last keyframe the nearest one holds.

Smoothing for automatic reframing: One-Euro filter -> dead zone (hold still while the subject
barely moves) -> pan-speed cap -> Ramer-Douglas-Peucker reduction to a few keyframes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

MIN_ZOOM, MAX_ZOOM = 1.0, 4.0
EASINGS = ("linear", "ease_in_out")
MAX_PAN_SPEED = 0.25  # source frame widths per second (acceptance: max pan ~25 % per second)

Keyframe = dict[str, float]
Segment = dict[str, float]


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def crop_size(src_w: float, src_h: float, out_aspect: float, zoom: float = 1.0) -> tuple[float, float]:
    """Crop width/height in source pixels for an output aspect (w/h) at a zoom level."""
    zoom = min(MAX_ZOOM, max(MIN_ZOOM, zoom))
    if src_w / src_h >= out_aspect:
        h = src_h
        w = src_h * out_aspect
    else:
        w = src_w
        h = src_w / out_aspect
    return w / zoom, h / zoom


def crop_rect(x: float, y: float, zoom: float, src_w: float, src_h: float, out_aspect: float) -> tuple[float, float, float, float]:
    """Normalized crop rectangle (left, top, width, height) for a center + zoom, clamped to the frame."""
    w, h = crop_size(src_w, src_h, out_aspect, zoom)
    cx = min(max(x * src_w, w / 2), src_w - w / 2)
    cy = min(max(y * src_h, h / 2), src_h - h / 2)
    return ((cx - w / 2) / src_w, (cy - h / 2) / src_h, w / src_w, h / src_h)


def clamp_center(x: float, y: float, zoom: float, src_w: float, src_h: float, out_aspect: float) -> tuple[float, float]:
    left, top, w, h = crop_rect(x, y, zoom, src_w, src_h, out_aspect)
    return left + w / 2, top + h / 2


def is_vertical(src_w: float, src_h: float, out_aspect: float, tolerance: float = 0.02) -> bool:
    """True when the source is already (about) as narrow as the output: no reframing needed."""
    return src_w / src_h <= out_aspect * (1 + tolerance)


# --------------------------------------------------------------------------
# interpolation
# --------------------------------------------------------------------------
def ease(u: float, easing: str = "linear") -> float:
    u = min(1.0, max(0.0, u))
    if easing == "ease_in_out":
        return u * u * (3 - 2 * u)
    return u


def interpolate(keyframes: Sequence[Keyframe], t: float, easing: str = "linear") -> tuple[float, float, float]:
    """(x, y, zoom) at clip time t. No keyframes -> centered, zoom 1."""
    if not keyframes:
        return 0.5, 0.5, 1.0
    kfs = sorted(keyframes, key=lambda k: k["t"])
    if t <= kfs[0]["t"]:
        k = kfs[0]
        return k["x"], k["y"], k.get("zoom", 1.0)
    if t >= kfs[-1]["t"]:
        k = kfs[-1]
        return k["x"], k["y"], k.get("zoom", 1.0)
    for a, b in zip(kfs, kfs[1:]):
        if a["t"] <= t <= b["t"]:
            span = b["t"] - a["t"]
            u = ease((t - a["t"]) / span if span > 0 else 1.0, easing)
            za, zb = a.get("zoom", 1.0), b.get("zoom", 1.0)
            return a["x"] + (b["x"] - a["x"]) * u, a["y"] + (b["y"] - a["y"]) * u, za + (zb - za) * u
    k = kfs[-1]  # unreachable for sorted input
    return k["x"], k["y"], k.get("zoom", 1.0)


# --------------------------------------------------------------------------
# clip time <-> source time
# --------------------------------------------------------------------------
def clip_duration(segments: Sequence[Segment]) -> float:
    return sum(max(0.0, s["end"] - s["start"]) for s in segments)


def to_clip_time(segments: Sequence[Segment], source_t: float) -> float | None:
    """Clip time of a source time, or None when it falls outside every segment."""
    offset = 0.0
    for s in segments:
        if s["start"] <= source_t <= s["end"]:
            return offset + source_t - s["start"]
        offset += s["end"] - s["start"]
    return None


def to_source_time(segments: Sequence[Segment], clip_t: float) -> float:
    """Source time of a clip time (clamped to the clip)."""
    if not segments:
        return clip_t
    offset = 0.0
    for s in segments:
        length = s["end"] - s["start"]
        if clip_t <= offset + length:
            return s["start"] + max(0.0, clip_t - offset)
        offset += length
    return segments[-1]["end"]


# --------------------------------------------------------------------------
# smoothing
# --------------------------------------------------------------------------
class OneEuro:
    """One-Euro filter (Casiez et al. 2012): little lag on fast moves, strong smoothing when still."""

    def __init__(self, min_cutoff: float = 0.3, beta: float = 0.6, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev: float | None = None
        self.dx_prev = 0.0
        self.t_prev: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, t: float, x: float) -> float:
        if self.x_prev is None or self.t_prev is None or t <= self.t_prev:
            self.x_prev, self.t_prev = x, t
            return x
        dt = t - self.t_prev
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t
        return x_hat


def one_euro(times: Sequence[float], values: Sequence[float], **kwargs: float) -> list[float]:
    f = OneEuro(**kwargs)
    return [f(t, v) for t, v in zip(times, values)]


def dead_zone(values: Sequence[float], width: float) -> list[float]:
    """Hold the output still until the input moves more than `width` away, then follow at that
    distance. Removes jitter of a subject that is (almost) still."""
    out: list[float] = []
    cur: float | None = None
    for v in values:
        if cur is None:
            cur = v
        elif v > cur + width:
            cur = v - width
        elif v < cur - width:
            cur = v + width
        out.append(cur)
    return out


def cap_speed(times: Sequence[float], values: Sequence[float], max_speed: float) -> list[float]:
    """Limit |dv/dt| to max_speed (forward pass, then backward pass so the move starts early
    enough to arrive in time instead of always lagging)."""
    if not values:
        return []
    fwd = [values[0]]
    for i in range(1, len(values)):
        step = max_speed * max(0.0, times[i] - times[i - 1])
        fwd.append(min(fwd[-1] + step, max(fwd[-1] - step, values[i])))
    out = fwd[:]
    for i in range(len(values) - 2, -1, -1):
        step = max_speed * max(0.0, times[i + 1] - times[i])
        out[i] = min(out[i + 1] + step, max(out[i + 1] - step, out[i]))
    return out


def _rdp(points: list[tuple[float, ...]], eps: float, scale_t: float) -> list[int]:
    """Indices kept by Ramer-Douglas-Peucker; distance measured on the value axes at equal t."""
    if len(points) < 3:
        return list(range(len(points)))
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        pa, pb = points[a], points[b]
        span = pb[0] - pa[0]
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            u = (points[i][0] - pa[0]) / span if span > 0 else 0.0
            d = max(abs(points[i][k] - (pa[k] + (pb[k] - pa[k]) * u)) for k in range(1, len(pa)))
            if d > best:
                best, idx = d, i
        if best > eps and idx > 0:
            keep.add(idx)
            stack += [(a, idx), (idx, b)]
    del scale_t
    return sorted(keep)


def reduce_keyframes(keyframes: Sequence[Keyframe], tolerance: float = 0.004) -> list[Keyframe]:
    """Drop keyframes that linear interpolation between their neighbours reproduces within
    `tolerance` (normalized units; zoom is compared /4 so 0.004 x == 0.016 zoom)."""
    if len(keyframes) < 3:
        return [dict(k) for k in keyframes]
    pts = [(k["t"], k["x"], k["y"], k.get("zoom", 1.0) / 4) for k in keyframes]
    return [dict(keyframes[i]) for i in _rdp(pts, tolerance, 1.0)]


def smooth_track(times: Sequence[float], xs: Sequence[float], ys: Sequence[float], *, zoom: float = 1.0,
                 src_w: float, src_h: float, out_aspect: float, max_speed: float = MAX_PAN_SPEED,
                 dead_x: float = 0.03, dead_y: float = 0.04, min_cutoff: float = 0.3, beta: float = 0.6,
                 tolerance: float = 0.004) -> list[Keyframe]:
    """Raw subject centers (normalized, sampled at `times`, clip time) -> few smooth keyframes."""
    if not times:
        return [{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": round(zoom, 3)}]
    # clamp first, so smoothing works on positions the crop can actually reach
    cx, cy = zip(*(clamp_center(x, y, zoom, src_w, src_h, out_aspect) for x, y in zip(xs, ys)))
    sx = cap_speed(times, dead_zone(one_euro(times, cx, min_cutoff=min_cutoff, beta=beta), dead_x), max_speed)
    # vertical speed in source heights/s, same physical limit as horizontal
    sy = cap_speed(times, dead_zone(one_euro(times, cy, min_cutoff=min_cutoff, beta=beta), dead_y),
                   max_speed * src_w / src_h)
    kfs = [{"t": round(t, 3), "x": round(x, 4), "y": round(y, 4), "zoom": round(zoom, 3)} for t, x, y in zip(times, sx, sy)]
    return reduce_keyframes(kfs, tolerance)


def max_pan_speed(keyframes: Sequence[Keyframe]) -> float:
    """Largest |dx/dt| between consecutive keyframes (source widths per second; cuts excluded)."""
    best = 0.0
    kfs = sorted(keyframes, key=lambda k: k["t"])
    for a, b in zip(kfs, kfs[1:]):
        dt = b["t"] - a["t"]
        if dt > 0.05:  # a jump within 50 ms is a deliberate cut, not a pan
            best = max(best, abs(b["x"] - a["x"]) / dt)
    return best


def as_keyframes(items: Sequence[dict[str, Any]]) -> list[Keyframe]:
    return [{"t": float(k["t"]), "x": float(k["x"]), "y": float(k["y"]), "zoom": float(k.get("zoom", 1.0))} for k in items]
