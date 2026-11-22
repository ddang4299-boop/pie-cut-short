"""Step 4 - Analyze: scene cuts, loudness peaks, silences, waveform peaks.

- Scene cuts: PySceneDetect ContentDetector on the 720p proxy (processed in chunks so the job can
  report progress and be cancelled).
- Loudness: ffmpeg `ebur128` momentary loudness (400 ms window, every 100 ms) on the 16 kHz WAV,
  reduced to one value per second; peaks = top 30 seconds with 5 s non-maximum suppression.
- Silences: ffmpeg `silencedetect` (min 0.4 s). The noise threshold adapts to the recording:
  quiet rooms and loud mixes need different thresholds.
- Waveform: max |sample| per 1/20 s from the WAV (for the Moments Review timeline).

Pure helpers (parse_*, per_second, find_peaks, silence_threshold, waveform_peaks) are unit-tested.

CLI (from backend/):  python -m pipeline.analyze <audio_16k.wav> [--video proxy.mp4]
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

ANALYZE_VERSION = 1
SILENCE_MIN = 0.4
PEAKS_TOP = 30
PEAK_SPACING = 5.0
WAVEFORM_RATE = 20  # peaks per second
LOUDNESS_FLOOR = -70.0  # LUFS used for "-inf" frames

ProgressFn = Callable[[float], None]
ProcessFn = Callable[[subprocess.Popen], None]

_EBUR_LINE = re.compile(r"t:\s*(?P<t>[\d.]+)\s+.*?M:\s*(?P<m>-?inf|-?[\d.]+)")
_SIL_START = re.compile(r"silence_start:\s*(?P<v>-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(?P<v>-?[\d.]+)")


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def parse_ebur128(lines: list[str] | Any) -> list[tuple[float, float]]:
    """(t, momentary LUFS) pairs from ffmpeg ebur128 stderr lines."""
    out: list[tuple[float, float]] = []
    for line in lines:
        m = _EBUR_LINE.search(line)
        if not m:
            continue
        val = m.group("m")
        lufs = LOUDNESS_FLOOR if "inf" in val else max(LOUDNESS_FLOOR, float(val))
        out.append((float(m.group("t")), lufs))
    return out


def per_second(frames: list[tuple[float, float]], duration: float) -> list[float]:
    """Max momentary loudness per whole second (index i covers [i, i+1))."""
    n = max(1, math.ceil(duration))
    values = [LOUDNESS_FLOOR] * n
    for t, lufs in frames:
        # ebur128's momentary value at t describes the 400 ms ending at t: attribute it to its middle
        i = int(max(0.0, t - 0.2))
        if 0 <= i < n and lufs > values[i]:
            values[i] = lufs
    return [round(v, 2) for v in values]


def relative(values: list[float]) -> list[float]:
    """Map LUFS values to 0..1 relative to this recording (10th percentile -> 0, max -> 1)."""
    if not values:
        return []
    arr = np.asarray(values, dtype=float)
    lo, hi = float(np.percentile(arr, 10)), float(arr.max())
    if hi - lo < 1e-6:
        return [0.0 for _ in values]
    return [round(float(x), 3) for x in np.clip((arr - lo) / (hi - lo), 0.0, 1.0)]


def find_peaks(rel: list[float], top: int = PEAKS_TOP, spacing: float = PEAK_SPACING) -> list[tuple[float, float]]:
    """Top-N loudest seconds, at least `spacing` seconds apart. Returns [(t_center, rel)] by time."""
    order = sorted(range(len(rel)), key=lambda i: rel[i], reverse=True)
    picked: list[int] = []
    for i in order:
        if len(picked) >= top or rel[i] <= 0:
            break
        if all(abs(i - j) >= spacing for j in picked):
            picked.append(i)
    return sorted((round(i + 0.5, 1), rel[i]) for i in picked)


def parse_silencedetect(lines: list[str] | Any, duration: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in lines:
        if m := _SIL_START.search(line):
            start = max(0.0, float(m.group("v")))
        elif (m := _SIL_END.search(line)) and start is not None:
            out.append((round(start, 3), round(min(duration, float(m.group("v"))), 3)))
            start = None
    if start is not None and duration - start >= SILENCE_MIN:
        out.append((round(start, 3), round(duration, 3)))  # file ends in silence
    return out


def silence_threshold(frame_rms_db: np.ndarray) -> float:
    """Adaptive silencedetect threshold (dBFS): a little above the recording's noise floor,
    clamped to [-55, -28] so a very loud mix or a very quiet room still behaves."""
    voiced = frame_rms_db[frame_rms_db > -90]
    if voiced.size == 0:
        return -50.0
    floor = float(np.percentile(voiced, 10))
    speech = float(np.percentile(voiced, 70))
    threshold = floor + max(6.0, (speech - floor) * 0.35)
    return round(float(min(-28.0, max(-55.0, threshold))), 1)


def waveform_peaks(samples: np.ndarray, sample_rate: int, rate: int = WAVEFORM_RATE) -> list[int]:
    """Max |sample| per 1/rate s, scaled 0..100 relative to the loudest block (compact JSON)."""
    block = max(1, sample_rate // rate)
    n = len(samples) // block
    if n == 0:
        return []
    peaks = np.abs(samples[: n * block].reshape(n, block)).max(axis=1).astype(float)
    top = peaks.max()
    if top <= 0:
        return [0] * n
    return np.round(peaks / top * 100).astype(int).tolist()


# --------------------------------------------------------------------------
# Audio reading
# --------------------------------------------------------------------------
def read_wav_stats(path: Path, rate: int = WAVEFORM_RATE) -> tuple[list[int], np.ndarray, float]:
    """Stream the 16-bit mono WAV once: waveform peaks, per-50ms RMS dBFS, duration."""
    with wave.open(str(path), "rb") as wf:
        sr, width, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        if width != 2:
            raise ValueError(f"expected 16-bit WAV, got {width * 8}-bit")
        total = wf.getnframes()
        block = max(1, sr // rate)
        rms_block = max(1, sr // 20)
        chunk = block * rms_block * 10  # multiple of both block sizes
        abs_peaks: list[np.ndarray] = []
        rms: list[np.ndarray] = []
        while True:
            raw = wf.readframes(chunk)
            if not raw:
                break
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                data = data.reshape(-1, channels).mean(axis=1)
            n = len(data) // block
            if n:
                abs_peaks.append(np.abs(data[: n * block].reshape(n, block)).max(axis=1))
            m = len(data) // rms_block
            if m:
                r = np.sqrt(np.mean(np.square(data[: m * rms_block].reshape(m, rms_block)), axis=1))
                rms.append(20 * np.log10(np.maximum(r, 1e-6)))
    peaks = np.concatenate(abs_peaks) if abs_peaks else np.zeros(0)
    top = float(peaks.max()) if peaks.size else 0.0
    wave_out = (np.round(peaks / top * 100).astype(int).tolist()) if top > 0 else [0] * len(peaks)
    rms_db = np.concatenate(rms) if rms else np.zeros(0)
    return wave_out, rms_db, total / sr if sr else 0.0


# --------------------------------------------------------------------------
# ffmpeg runners
# --------------------------------------------------------------------------
def _run_stderr(cmd: list[str], on_process: ProcessFn | None, on_line: Callable[[str], None] | None = None) -> list[str]:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_process:
        on_process(proc)
    lines: list[str] = []
    assert proc.stderr is not None
    for line in proc.stderr:
        lines.append(line)
        if on_line:
            on_line(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}: {''.join(lines[-5:]).strip()[:300]}")
    return lines


def loudness_frames(audio: Path, *, duration: float, ffmpeg: str = "ffmpeg", on_progress: ProgressFn | None = None,
                    on_process: ProcessFn | None = None) -> list[tuple[float, float]]:
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(audio), "-af", "ebur128=peak=none",
           "-f", "null", "-"]
    frames: list[tuple[float, float]] = []

    def on_line(line: str) -> None:
        got = parse_ebur128([line])
        if got:
            frames.extend(got)
            if on_progress and duration and len(frames) % 200 == 0:
                on_progress(min(1.0, got[-1][0] / duration))

    _run_stderr(cmd, on_process, on_line)
    return frames


def detect_silences(audio: Path, *, threshold_db: float, duration: float, ffmpeg: str = "ffmpeg",
                    on_process: ProcessFn | None = None) -> list[tuple[float, float]]:
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(audio),
           "-af", f"silencedetect=noise={threshold_db}dB:d={SILENCE_MIN}", "-f", "null", "-"]
    return parse_silencedetect(_run_stderr(cmd, on_process), duration)


def scene_cuts(video: Path, *, on_progress: ProgressFn | None = None, chunk_s: float = 30.0,
               threshold: float = 27.0) -> list[float]:
    """Scene-cut times (seconds). on_progress may raise to cancel between chunks."""
    from scenedetect import ContentDetector, SceneManager, open_video

    stream = open_video(str(video))
    total = stream.duration.seconds if stream.duration is not None else 0.0
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=15))
    manager.auto_downscale = True
    end = 0.0
    while True:
        end += chunk_s
        processed = manager.detect_scenes(video=stream, end_time=end if total else None)
        if on_progress and total:
            on_progress(min(1.0, end / total))
        if not total or end >= total or processed == 0:
            break
    scenes = manager.get_scene_list(start_in_scene=True)
    return [round(start.seconds, 2) for start, _ in scenes[1:]]


# --------------------------------------------------------------------------
# Whole step
# --------------------------------------------------------------------------
def analyze(
    audio: Path | None,
    video: Path | None,
    *,
    duration: float,
    ffmpeg: str = "ffmpeg",
    on_progress: Callable[[float, str | None], None] | None = None,
    on_process: ProcessFn | None = None,
    log: Callable[[str], None] | None = None,
    scenes_fn: Callable[..., list[float]] | None = None,
) -> tuple[dict[str, Any], list[int]]:
    """Returns (analysis dict, waveform peaks). `audio` may be None (no audio track).

    `scenes_fn(video, on_progress=...)` defaults to in-process `scene_cuts`; the app passes a
    function that runs it in a child process (see pipeline/vision_worker.py).
    """
    report = on_progress or (lambda _f, _s=None: None)
    result: dict[str, Any] = {"version": ANALYZE_VERSION, "duration": duration, "scene_cuts": [], "loudness": [],
                              "peaks": [], "silences": [], "silence_threshold_db": None}
    waveform: list[int] = []
    if audio is not None and audio.exists():
        report(0.0, "reading_audio")
        waveform, rms_db, _ = read_wav_stats(audio)
        threshold = silence_threshold(rms_db)
        result["silence_threshold_db"] = threshold
        report(0.05, "loudness")
        frames = loudness_frames(audio, duration=duration, ffmpeg=ffmpeg, on_process=on_process,
                                 on_progress=lambda f: report(0.05 + 0.2 * f, None))
        loud = per_second(frames, duration)
        rel = relative(loud)
        result["loudness"] = loud
        result["peaks"] = [[t, r] for t, r in find_peaks(rel)]
        report(0.25, "silences")
        result["silences"] = [[a, b] for a, b in detect_silences(audio, threshold_db=threshold, duration=duration,
                                                                  ffmpeg=ffmpeg, on_process=on_process)]
        if log:
            log(f"loudness: {len(frames)} frames, {len(result['peaks'])} peaks; "
                f"silences: {len(result['silences'])} at {threshold} dB")
    if video is not None and video.exists():
        report(0.3, "scenes")
        find_cuts = scenes_fn or scene_cuts
        result["scene_cuts"] = find_cuts(video, on_progress=lambda f: report(0.3 + 0.55 * f, None))
        if log:
            log(f"scene cuts: {len(result['scene_cuts'])}")
    report(0.85, None)
    return result, waveform


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Analyze audio (loudness, silences) and video (scene cuts).")
    p.add_argument("audio", type=Path)
    p.add_argument("--video", type=Path)
    p.add_argument("--ffmpeg", default="ffmpeg")
    args = p.parse_args(argv)
    with wave.open(str(args.audio), "rb") as wf:
        duration = wf.getnframes() / wf.getframerate()
    result, wave_peaks = analyze(args.audio, args.video, duration=duration, ffmpeg=args.ffmpeg,
                                 log=lambda m: print(m, file=sys.stderr))
    summary = {k: v for k, v in result.items() if k != "loudness"}
    summary["waveform_points"] = len(wave_peaks)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
