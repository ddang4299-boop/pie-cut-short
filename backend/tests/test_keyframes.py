"""Keyframe geometry, interpolation, time mapping and smoothing (pure logic)."""

import math
import random

import pytest

from pipeline import keyframes as kf

ASPECT = 9 / 16


def test_crop_size_fits_output_aspect_inside_source():
    w, h = kf.crop_size(1920, 1080, ASPECT)
    assert h == 1080 and w == pytest.approx(607.5)
    w2, h2 = kf.crop_size(1920, 1080, ASPECT, zoom=2)
    assert (w2, h2) == pytest.approx((w / 2, h / 2))
    # a source narrower than the output: full width
    w3, h3 = kf.crop_size(720, 1600, ASPECT)
    assert w3 == 720 and h3 == pytest.approx(1280)
    # zoom is clamped
    assert kf.crop_size(1920, 1080, ASPECT, zoom=0.2) == kf.crop_size(1920, 1080, ASPECT, zoom=1)


def test_crop_rect_is_clamped_inside_the_frame():
    left, top, w, h = kf.crop_rect(0.0, 0.5, 1.0, 1920, 1080, ASPECT)
    assert left == 0 and top == 0 and h == 1
    left, _, w, _ = kf.crop_rect(1.0, 0.5, 1.0, 1920, 1080, ASPECT)
    assert left + w == pytest.approx(1.0)
    # zoomed crop can move vertically
    _, top, _, h = kf.crop_rect(0.5, 0.9, 2.0, 1920, 1080, ASPECT)
    assert top + h == pytest.approx(1.0) and h == pytest.approx(0.5)
    assert kf.clamp_center(0.0, 0.0, 1.0, 1920, 1080, ASPECT) == pytest.approx((607.5 / 2 / 1920, 0.5))


def test_is_vertical():
    assert kf.is_vertical(1080, 1920, ASPECT)
    assert not kf.is_vertical(1920, 1080, ASPECT)


def test_interpolate_linear_and_ease():
    kfs = [{"t": 0, "x": 0.2, "y": 0.5, "zoom": 1}, {"t": 2, "x": 0.6, "y": 0.5, "zoom": 2}]
    assert kf.interpolate(kfs, -1) == (0.2, 0.5, 1)
    assert kf.interpolate(kfs, 5) == (0.6, 0.5, 2)
    assert kf.interpolate(kfs, 1) == pytest.approx((0.4, 0.5, 1.5))
    x, _, _ = kf.interpolate(kfs, 0.5, "ease_in_out")
    assert x == pytest.approx(0.2 + 0.4 * 0.15625)
    assert kf.interpolate(kfs, 1, "ease_in_out") == pytest.approx((0.4, 0.5, 1.5))  # symmetric midpoint
    # unsorted input, missing zoom, no keyframes
    assert kf.interpolate([kfs[1], {"t": 0, "x": 0.2, "y": 0.5}], 1) == pytest.approx((0.4, 0.5, 1.5))
    assert kf.interpolate([], 3) == (0.5, 0.5, 1.0)


def test_ease_is_monotonic_and_bounded():
    vals = [kf.ease(i / 20, "ease_in_out") for i in range(21)]
    assert vals[0] == 0 and vals[-1] == 1 and all(a <= b for a, b in zip(vals, vals[1:]))
    assert kf.ease(2.0) == 1 and kf.ease(-1.0) == 0


def test_time_mapping_over_segments():
    segs = [{"start": 10, "end": 20}, {"start": 30, "end": 35}]
    assert kf.clip_duration(segs) == 15
    assert kf.to_clip_time(segs, 12) == 2
    assert kf.to_clip_time(segs, 31) == 11
    assert kf.to_clip_time(segs, 25) is None
    assert kf.to_source_time(segs, 2) == 12
    assert kf.to_source_time(segs, 11) == 31
    assert kf.to_source_time(segs, 99) == 35
    assert kf.to_source_time([], 4) == 4
    for t in (0, 3.3, 10, 12.5, 15):
        assert kf.to_clip_time(segs, kf.to_source_time(segs, t)) == pytest.approx(t)


def test_one_euro_smooths_noise_but_follows_moves():
    rng = random.Random(1)
    times = [i / 5 for i in range(100)]
    noisy = [0.5 + rng.uniform(-0.02, 0.02) for _ in times]
    out = kf.one_euro(times, noisy)
    spread_in = max(noisy[20:]) - min(noisy[20:])
    spread_out = max(out[20:]) - min(out[20:])
    assert spread_out < spread_in * 0.6
    step = [0.2 if t < 10 else 0.8 for t in times]
    out = kf.one_euro(times, step)
    assert out[-1] == pytest.approx(0.8, abs=0.01)


def test_dead_zone_holds_still_subject():
    vals = [0.5, 0.51, 0.49, 0.52, 0.48, 0.5]
    assert kf.dead_zone(vals, 0.03) == [0.5] * 6
    assert kf.dead_zone([0.5, 0.6], 0.03) == pytest.approx([0.5, 0.57])


def test_cap_speed_limits_pan_and_arrives():
    times = [i / 5 for i in range(30)]
    vals = [0.2 if t < 3 else 0.8 for t in times]
    out = kf.cap_speed(times, vals, 0.25)
    for (t0, a), (t1, b) in zip(zip(times, out), zip(times[1:], out[1:])):
        assert abs(b - a) <= 0.25 * (t1 - t0) + 1e-9
    assert out[0] < 0.21 or out[-1] > 0.79  # ends reachable within the window


def test_reduce_keyframes_keeps_shape():
    line = [{"t": i, "x": 0.1 * i, "y": 0.5, "zoom": 1} for i in range(6)]
    assert len(kf.reduce_keyframes(line)) == 2
    corner = line + [{"t": 6, "x": 0.4, "y": 0.5, "zoom": 1}]
    reduced = kf.reduce_keyframes(corner)
    assert [k["t"] for k in reduced] == [0, 5, 6]


def test_smooth_track_still_subject_has_no_jitter():
    rng = random.Random(7)
    times = [i / 5 for i in range(150)]  # 30 s at 5 fps
    xs = [0.55 + rng.gauss(0, 0.01) for _ in times]
    ys = [0.4 + rng.gauss(0, 0.01) for _ in times]
    out = kf.smooth_track(times, xs, ys, src_w=1920, src_h=1080, out_aspect=ASPECT)
    assert len(out) <= 4
    xs_out = [kf.interpolate(out, t)[0] for t in times[10:]]
    assert max(xs_out) - min(xs_out) < 0.02


def test_smooth_track_moving_subject_respects_speed_cap():
    times = [i / 5 for i in range(100)]
    xs = [0.2 if t < 8 else 0.8 for t in times]  # subject jumps across the frame
    out = kf.smooth_track(times, xs, [0.5] * len(times), src_w=1920, src_h=1080, out_aspect=ASPECT)
    assert kf.max_pan_speed(out) <= kf.MAX_PAN_SPEED + 0.01
    assert kf.interpolate(out, times[-1])[0] > 0.75
    assert kf.interpolate(out, 0)[0] < 0.25
    assert 2 <= len(out) <= 12


def test_smooth_track_empty_is_centered():
    assert kf.smooth_track([], [], [], src_w=1920, src_h=1080, out_aspect=ASPECT) == [
        {"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0}]


def test_as_keyframes_coerces_numbers():
    assert kf.as_keyframes([{"t": "1", "x": 0, "y": 1}]) == [{"t": 1.0, "x": 0.0, "y": 1.0, "zoom": 1.0}]
    assert not math.isnan(kf.max_pan_speed([]))


def test_shared_vectors_match_the_frontend_copy():
    """frontend/src/lib/edl.ts is checked against the same file (edl.test.ts): preview == render."""
    import json
    from pathlib import Path

    vectors = json.loads((Path(__file__).resolve().parents[2] / "frontend/src/lib/edl.vectors.json").read_text("utf-8"))
    kfs, segs = vectors["keyframes"], vectors["segments"]
    for case in vectors["interpolate"]:
        assert kf.interpolate(kfs, case["t"], case["easing"]) == pytest.approx(case["out"], abs=1e-9)
    for case in vectors["crop_rect"]:
        x, y, z, w, h = case["in"]
        assert kf.crop_rect(x, y, z, w, h, ASPECT) == pytest.approx(case["out"], abs=1e-9)
    for case in vectors["to_clip"]:
        got = kf.to_clip_time(segs, case["t"])
        assert (got is None and case["out"] is None) or got == pytest.approx(case["out"], abs=1e-9)
    for case in vectors["to_source"]:
        assert kf.to_source_time(segs, case["t"]) == pytest.approx(case["out"], abs=1e-9)
