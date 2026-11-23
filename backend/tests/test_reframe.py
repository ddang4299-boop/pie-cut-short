"""Reframe planning on synthetic detections, cursor tracking on synthetic frames, rough cut."""

import json
import random
from pathlib import Path

import numpy as np
import pytest

from pipeline import cursor_track, keyframes as kf, prepare, reframe
from tests.conftest import make_media, needs_ffmpeg

W, H, ASPECT = 1920, 1080, 9 / 16
MODELS = Path(__file__).resolve().parents[2] / "data" / "models"  # face model downloaded once


def face(cx, cy, size=0.12, score=0.9):
    return (cx - size / 2, cy - size / 2, size, size * 16 / 9, score)


def test_default_mode_by_content_type():
    assert reframe.default_mode("talking_head") == "auto"
    assert reframe.default_mode("podcast") == "auto"
    assert reframe.default_mode("gameplay_facecam") == "split"
    assert reframe.default_mode("screen_recording") == "cursor"
    assert reframe.default_mode(None) == "auto"


def test_plan_faces_follows_a_moving_face_smoothly():
    rng = random.Random(3)
    samples = []
    for i in range(150):  # 30 s at 5 fps: still, walks to the right, still
        t = i / 5
        x = 0.3 if t < 10 else (0.3 + 0.4 * min(1.0, (t - 10) / 8))
        samples.append((t, [face(x + rng.gauss(0, 0.004), 0.4 + rng.gauss(0, 0.004))]))
    plan = reframe.plan_faces(samples, src_w=W, src_h=H, out_aspect=ASPECT)
    kfs = plan["keyframes"]
    assert plan["found_ratio"] == 1.0
    assert kf.max_pan_speed(kfs) <= kf.MAX_PAN_SPEED + 0.01
    assert len(kfs) <= 15
    assert kf.interpolate(kfs, 5)[0] == pytest.approx(0.3, abs=0.04)
    assert kf.interpolate(kfs, 29)[0] == pytest.approx(0.7, abs=0.04)
    still = [kf.interpolate(kfs, t)[0] for t in np.arange(1, 9.5, 0.2)]
    assert max(still) - min(still) < 0.01  # no jitter while the subject is still


def test_plan_faces_ignores_a_passing_face_and_holds_when_lost():
    samples = []
    for i in range(50):
        t = i / 5
        boxes = [face(0.6, 0.4)] if not (4 <= t < 6) else []  # lost for 2 s
        if 2 <= t < 3:
            boxes.append(face(0.15, 0.5, size=0.05, score=0.6))  # small background face
        samples.append((t, boxes))
    kfs = reframe.plan_faces(samples, src_w=W, src_h=H, out_aspect=ASPECT)["keyframes"]
    xs = [kf.interpolate(kfs, t)[0] for t in np.arange(0, 10, 0.2)]
    assert max(xs) - min(xs) < 0.02


def test_plan_faces_without_detections_is_centered():
    plan = reframe.plan_faces([(i / 5, []) for i in range(10)], src_w=W, src_h=H, out_aspect=ASPECT)
    assert plan["found_ratio"] == 0 and kf.interpolate(plan["keyframes"], 1)[:2] == (0.5, 0.5)


def test_plan_speakers_cuts_to_the_active_speaker():
    samples, activity = [], []
    for i in range(100):  # left speaks for 10 s, then right
        t = i / 5
        samples.append((t, [face(0.25, 0.4), face(0.75, 0.4)]))
        left_talks = t < 10
        activity.append({0: 0.05 if left_talks else 0.005, 1: 0.005 if left_talks else 0.05})
    plan = reframe.plan_speakers(samples, activity, src_w=W, src_h=H, out_aspect=ASPECT)
    kfs = plan["keyframes"]
    assert plan["shots"] == 2
    assert kf.interpolate(kfs, 5)[0] == pytest.approx(0.25, abs=0.03)
    assert kf.interpolate(kfs, 15)[0] == pytest.approx(0.75, abs=0.03)
    # the switch is a cut (within ~0.6 s), not a slow pan across the table
    assert kf.max_pan_speed(kfs) <= kf.MAX_PAN_SPEED + 0.01
    assert kf.interpolate(kfs, 10.6)[0] > 0.7


def test_plan_speakers_does_not_flicker():
    samples, activity = [], []
    rng = random.Random(5)
    for i in range(100):
        samples.append((i / 5, [face(0.25, 0.4), face(0.75, 0.4)]))
        activity.append({0: 0.03 + rng.uniform(-0.01, 0.01), 1: 0.03 + rng.uniform(-0.01, 0.01)})
    assert reframe.plan_speakers(samples, activity, src_w=W, src_h=H, out_aspect=ASPECT)["shots"] <= 3


def test_fit_region_aspect_and_bounds():
    region = reframe.fit_region(0.95, 0.05, 0.2, 1.4, W, H)
    x, y, w, h = region
    assert x + w <= 1.0001 and y >= 0 and (w * W) / (h * H) == pytest.approx(1.4, rel=0.01)
    big = reframe.fit_region(0.5, 0.5, 3.0, 0.9375, W, H)
    assert big[3] == pytest.approx(1.0) and (big[2] * W) / (big[3] * H) == pytest.approx(0.9375, rel=0.01)


def test_plan_split_finds_the_webcam():
    samples = [(i / 5, [face(0.85, 0.2, size=0.08)]) for i in range(30)]
    plan = reframe.plan_split(samples, src_w=W, src_h=H, out_w=1080, out_h=1920)
    top, bottom = plan["split"]["top"], plan["split"]["bottom"]
    top_aspect, bottom_aspect = reframe.split_aspects(1080, 1920, plan["split"]["ratio"])
    assert top[0] < 0.85 < top[0] + top[2] and top[1] < 0.2 < top[1] + top[3]
    assert (top[2] * W) / (top[3] * H) == pytest.approx(top_aspect, rel=0.01)
    assert (bottom[2] * W) / (bottom[3] * H) == pytest.approx(bottom_aspect, rel=0.01)
    assert plan["found_ratio"] == 1.0
    # nothing found: default regions, still the right shapes
    empty = reframe.plan_split([(0, [])], src_w=W, src_h=H, out_w=1080, out_h=1920)
    assert empty["found_ratio"] == 0 and len(empty["split"]["top"]) == 4


# --- cursor ------------------------------------------------------------------
def screen(cursor=None, shift=0):
    """Synthetic 640x360 screen: static UI texture plus a small bright cursor."""
    rng = np.random.default_rng(0)
    img = np.full((360, 640), 200, np.uint8)
    img[20:340:40, :] = 120  # lines of "text"
    img[:, 100:102] = 90
    img = np.roll(img, shift, axis=0)
    noise = rng.integers(0, 6, img.shape, dtype=np.uint8)
    img = (img + noise).astype(np.uint8)
    if cursor:
        x, y = int(cursor[0] * 640), int(cursor[1] * 360)
        img[y:y + 12, x:x + 7] = 20
    return img


def test_find_cursor_detects_small_moves_and_ignores_scrolls():
    a, b = screen((0.30, 0.40)), screen((0.32, 0.41))
    p = cursor_track.find_cursor(a, b)
    assert p is not None and p[0] == pytest.approx(0.31, abs=0.03) and p[1] == pytest.approx(0.42, abs=0.04)
    assert cursor_track.find_cursor(a, a) is None  # nothing moved
    assert cursor_track.find_cursor(screen((0.3, 0.4)), screen((0.3, 0.4), shift=17)) is None  # scroll


def test_cursor_track_follows_and_holds():
    frames, path = [], []
    for i in range(100):  # 10 s at 10 fps; cursor still for the last 3 s
        t = i / 10
        u = min(1.0, t / 7)
        pos = (0.2 + 0.6 * u, 0.3 + 0.3 * u)
        path.append(pos)
        frames.append((t, screen(pos)))
    points = cursor_track.track(frames)
    found = [p for _, p in points if p]
    assert len(found) >= 50
    plan = cursor_track.plan(points, src_w=W, src_h=H, out_aspect=ASPECT)
    assert plan["fit"] == "fill" and plan["info"]["method"] == "cursor"
    kfs = plan["keyframes"]
    assert all(k["zoom"] == cursor_track.ZOOM for k in kfs)
    assert kf.max_pan_speed(kfs) <= kf.MAX_PAN_SPEED + 0.01
    # the cursor stays inside the crop once the camera catches up
    for t, (cx, cy) in zip([i / 10 for i in range(100)], path):
        if t < 1:
            continue
        x, y, z = kf.interpolate(kfs, t)
        left, top, w, h = kf.crop_rect(x, y, z, W, H, ASPECT)
        assert left - 0.02 <= cx <= left + w + 0.02, (t, cx, left, w)


def test_cursor_plan_falls_back_to_blur():
    plan = cursor_track.plan([(i / 10, None) for i in range(50)], src_w=W, src_h=H, out_aspect=ASPECT)
    assert plan["fit"] == "blur" and plan["info"]["method"] == "blur_fallback"


# --- frames + worker + rough cut ----------------------------------------------
@needs_ffmpeg
def test_iter_frames_samples_segments_in_clip_time(tmp_path):
    media = make_media(tmp_path / "v.mp4", seconds=4)
    out = list(reframe.iter_frames(media, [{"start": 0.5, "end": 1.5}, {"start": 2.5, "end": 3.0}], fps=5))
    times = [round(c, 2) for c, _, _ in out]
    assert 6 <= len(out) <= 9
    assert times == sorted(times) and times[-1] <= 1.55
    assert all(0.45 <= s <= 1.55 or 2.45 <= s <= 3.05 for _, s, _ in out)


@needs_ffmpeg
def test_vision_worker_reframe_runs_end_to_end(tmp_path, capsys):
    from pipeline import vision_worker

    media = make_media(tmp_path / "v.mp4", seconds=3)
    code = vision_worker.main(["reframe", str(media), "--segments", json.dumps([{"start": 0, "end": 2.5}]),
                               "--content-type", "talking_head", "--src-w", "320", "--src-h", "240",
                               "--models-dir", str(MODELS)])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert code == 0, lines[-1]
    data = lines[-1]["data"]
    assert data["mode"] == "auto" and data["keyframes"] and data["fit"] == "fill"
    assert data["info"]["method"] in ("face", "person", "center")


@needs_ffmpeg
def test_rough_cut_pads_and_caches(tmp_path):
    media = make_media(tmp_path / "v.mp4", seconds=6)
    meta = prepare.rough_cut(media, tmp_path / "clip", start=2.5, end=3.5, duration=6.0)
    assert (meta["requested_start"], meta["requested_end"]) == (0.5, 5.5)
    assert 0.0 <= meta["source_start"] <= 0.5
    out = tmp_path / "clip" / "rough.mp4"
    mtime = out.stat().st_mtime_ns
    again = prepare.rough_cut(media, tmp_path / "clip", start=2.5, end=3.5, duration=6.0)
    assert again == meta and out.stat().st_mtime_ns == mtime  # cached
    assert prepare.rough_range(1.0, 59.5, 60.0) == (0.0, 60.0)
