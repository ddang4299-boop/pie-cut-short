"""End-to-end run on samples/: the full pipeline in-process, then a report of the acceptance metrics.

Usage (from the repo root):

    .venv\\Scripts\\python scripts\\e2e_sample.py [--clips 1] [--renderer remotion|ffmpeg]
                                               [--whisper-model large-v3] [--device auto]
                                               [--only NAME] [--keep]

Per video in samples/ (mp4, mov, mkv, webm, m4v):
  import -> preflight -> Start (proxy, transcribe, content type, analyze, moments with the offline
  `heuristic` provider) -> approve the best N moments (snap, prepare, reframe) -> render.

Everything runs in a throw-away data folder (data/e2e/run_<time>/, removed unless --keep). The
whisper models in data/models are shared, so nothing is downloaded when they are already there.
No paid API is called: moments use the heuristic provider, ElevenLabs is mocked, voiceover is off.

The report is printed and saved as data/e2e/report_<time>.json. Exit code 1 when a metric fails.
Metrics that need a browser (preview response time) or a human (phone playback) are listed as N/A.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LOCALES = ROOT / "frontend" / "src" / "locales"
CAPTION_FONTS = BACKEND / "assets" / "fonts"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

# Every Vietnamese letter (all tones, both cases): what "diacritics 100% correct" has to cover.
VI_LETTERS = "aàảãáạăằẳẵắặâầẩẫấậbcdđeèẻẽéẹêềểễếệghiìỉĩíịklmnoòỏõóọôồổỗốộơờởỡớợpqrstuùủũúụưừửữứựvxyỳỷỹýỵ"
VI_SENTENCE = "Tiếng Việt có dấu: Đường phố Hà Nội mùa thu, người ơi ở lại đừng về! Ước mơ của bạn là gì?"

# Acceptance targets (CLAUDE.md)
TRANSCRIBE_30MIN_GPU = 180.0  # s for a 30-min video on a mid-range GPU
CPU_SLOWDOWN_OK = 5.0  # CPU may be 3-5x slower
RENDER_RATIO = 2.0  # a 45 s clip in <= 90 s
LUFS_TARGET, LUFS_TOL, TRUE_PEAK_MAX = -14.0, 1.0, -1.0
SSIM_MIN = 0.95
CAPTION_OFFSET_MAX = 0.100
PAN_MAX = 0.25  # frame widths per second
INSIDE_WORD_EPS = 0.01


@dataclass
class Metric:
    name: str
    status: str  # PASS | FAIL | WARN | N/A
    value: str
    target: str
    note: str = ""


@dataclass
class ClipReport:
    id: str
    duration: float
    segments: list[dict[str, float]]
    crop_mode: str | None = None
    renderer: str | None = None
    encoder: str | None = None
    render_seconds: float | None = None
    lufs: float | None = None
    true_peak: float | None = None
    ssim_min: float | None = None
    cuts_inside_words: int = 0
    cut_points: int = 0
    caption_word_drift: float | None = None
    srt_drift: float | None = None
    onset_offsets: list[float] = field(default_factory=list)
    max_pan: float | None = None
    jitter_reversals: int | None = None
    uncovered_chars: str = ""
    non_nfc_words: int = 0
    srt_matches: bool | None = None
    error: str | None = None


@dataclass
class SampleReport:
    name: str
    duration: float | None = None
    size: str | None = None
    preflight_ok: bool | None = None
    status: str | None = None
    steps: dict[str, float] = field(default_factory=dict)
    step_states: dict[str, str] = field(default_factory=dict)
    words: int = 0
    language: str | None = None
    whisper_model: str | None = None
    whisper_device: str | None = None
    whisper_timing: dict[str, Any] | None = None  # {load_s, transcribe_s, batched} from the transcript
    content_type: str | None = None
    moments: int = 0
    clips: list[ClipReport] = field(default_factory=list)
    error: str | None = None


# ----------------------------------------------------------------------------- setup


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--samples", type=Path, default=ROOT / "samples", help="folder with the test videos")
    p.add_argument("--clips", type=int, default=1, help="moments to approve and render per video (default 1)")
    p.add_argument("--renderer", choices=("remotion", "ffmpeg"), default=None, help="default: the Settings default")
    p.add_argument("--whisper-model", default=None, help="override WHISPER_MODEL for this run")
    p.add_argument("--device", default=None, choices=("auto", "cuda", "mps", "cpu"), help="override DEVICE")
    p.add_argument("--only", default=None, help="only videos whose file name contains this text")
    p.add_argument("--keep", action="store_true", help="keep the run's data folder (media, DB, renders)")
    return p.parse_args()


def setup_env(args: argparse.Namespace, stamp: str) -> Path:
    """Point the backend at a fresh data folder before any app module is imported."""
    run_dir = ROOT / "data" / "e2e" / f"run_{stamp}"
    os.environ.update(DATA_DIR=str(run_dir), START_JOB_WORKERS="false", LLM_PROVIDER="heuristic", ELEVENLABS_MOCK="1")
    if args.whisper_model:
        os.environ["WHISPER_MODEL"] = args.whisper_model
    if args.device:
        os.environ["DEVICE"] = args.device
    sys.path.insert(0, str(BACKEND))
    # reuse the app's Remotion bundle (keyed by a hash of remotion/src): bundling happens once per
    # code change (~40 s), not once per render, so the render time below is the everyday one
    bundles = ROOT / "data" / "cache" / "remotion_bundle"
    if bundles.is_dir():
        shutil.copytree(bundles, run_dir / "cache" / "remotion_bundle", dirs_exist_ok=True)
    return run_dir


# ----------------------------------------------------------------------------- measurements (pure)


def cuts_inside_words(segments: list[dict[str, float]], words: list[dict[str, Any]]) -> tuple[int, int]:
    """(cut points that fall strictly inside a spoken word, total cut points)."""
    points = [p for s in segments for p in (s["start"], s["end"])]
    inside = sum(1 for t in points if any(w["start"] + INSIDE_WORD_EPS < t < w["end"] - INSIDE_WORD_EPS for w in words))
    return inside, len(points)


def pan_profile(keyframes: list[dict[str, float]], easing: str, duration: float, fps: float = 30.0) -> tuple[float, int]:
    """(largest crop-center move over any 1 s window in frame widths, small back-and-forth reversals).

    A reversal counts as jitter when the crop turns around after moving less than 0.5 % of the frame.
    """
    from pipeline import keyframes as kf

    if not keyframes or duration <= 0:
        return 0.0, 0
    n = max(2, int(duration * fps) + 1)
    xs = [kf.interpolate(keyframes, min(duration, i / fps), easing)[0] for i in range(n)]
    win = max(1, int(round(fps)))
    max_pan = max((abs(xs[i + win] - xs[i]) for i in range(n - win)), default=abs(xs[-1] - xs[0]) * fps / max(1, n - 1))
    jitter, direction, swing = 0, 0, 0.0
    for a, b in zip(xs, xs[1:]):
        d = b - a
        if abs(d) < 1e-6:
            continue
        s = 1 if d > 0 else -1
        if direction and s != direction:
            if swing < 0.005:
                jitter += 1
            swing = 0.0
        direction = s
        swing += abs(d)
    return max_pan, jitter


def audio_db(media: Path, ffmpeg: str = "ffmpeg", rate: int = 16000, hop: float = 0.01) -> list[float]:
    """Short-time level (dBFS, 20 ms window every 10 ms) of the media's audio, mono."""
    import numpy as np

    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(media), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    step, size = int(rate * hop), int(rate * hop * 2)
    if len(x) < size:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(x, size)[::step]
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return list(20 * np.log10(rms + 1e-9))


def onset_offsets(pages: list[dict[str, Any]], levels: list[float], hop: float = 0.01, min_gap: float = 0.25) -> list[float]:
    """Caption page start minus the speech onset heard in the audio, for pages that follow a pause.

    Positive = the caption appears after the speech starts. Only clear onsets count: the page must
    follow at least `min_gap` s without words, and the audio must be quiet for 100 ms before it.
    """
    if not levels:
        return []
    thr = max(levels) - 30.0
    out: list[float] = []
    prev_end = 0.0
    for i, page in enumerate(pages):
        start = page["start"]
        gap = start - (prev_end if i else 0.0)
        prev_end = page["end"]
        if gap < min_gap:
            continue
        lo, hi = int((start - 0.3) / hop), int((start + 0.3) / hop)
        for k in range(max(10, lo), min(len(levels), hi)):
            if levels[k] >= thr and all(v < thr for v in levels[k - 10:k]):
                out.append(round(start - k * hop, 3))
                break
    return out


def font_cmaps() -> dict[str, set[int]]:
    from fontTools.ttLib import TTFont

    out = {}
    for path in sorted(CAPTION_FONTS.glob("*.ttf")):
        with TTFont(path, lazy=True) as font:
            out[path.name] = set(font.getBestCmap() or {})
    return out


def uncovered(text: str, cmaps: dict[str, set[int]]) -> str:
    """Characters of `text` missing from at least one caption font (spaces ignored)."""
    chars = {c for c in unicodedata.normalize("NFC", text) if not c.isspace()}
    return "".join(sorted(c for c in chars if any(ord(c) not in cmap for cmap in cmaps.values())))


def locale_parity() -> tuple[bool, str]:
    def flat(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in d.items():
            key = f"{prefix}{k}"
            out.update(flat(v, key + ".") if isinstance(v, dict) else {key: v})
        return out

    en = flat(json.loads((LOCALES / "en.json").read_text("utf-8")))
    vi = flat(json.loads((LOCALES / "vi.json").read_text("utf-8")))
    only_en, only_vi = sorted(set(en) - set(vi)), sorted(set(vi) - set(en))
    empty = sorted(k for k, v in {**en, **vi}.items() if not str(v).strip())
    vars_ = re.compile(r"{{\s*(\w+)\s*}}")
    mismatch = sorted(k for k in set(en) & set(vi) if set(vars_.findall(str(en[k]))) != set(vars_.findall(str(vi[k]))))
    ok = not (only_en or only_vi or empty or mismatch)
    detail = f"{len(en)} keys each" if ok else (
        f"only EN: {only_en[:5]} · only VI: {only_vi[:5]} · empty: {empty[:5]} · placeholders differ: {mismatch[:5]}")
    return ok, detail


def leftover_temp_files(run_dir: Path) -> list[str]:
    from app import jobs

    found = {p for pattern in jobs.TEMP_PATTERNS for p in run_dir.rglob(pattern)}
    found |= {p for p in run_dir.rglob("render_work") if p.is_dir() and any(p.iterdir())}
    return sorted(str(p.relative_to(run_dir)) for p in found)


# ----------------------------------------------------------------------------- pipeline driver


def run_queue(lane: str) -> int:
    from app import jobs

    return jobs.get_queue().run_all(lane)


def run_sample(client: Any, path: Path, args: argparse.Namespace, step_times: dict[str, dict[str, float]],
               cmaps: dict[str, set[int]]) -> SampleReport:
    from app import clips_service, tasks
    from pipeline import audio_mix
    from pipeline import captions as cap

    rep = SampleReport(name=path.name)
    r = client.post("/sources/import", json={"location": str(path.resolve())})
    if r.status_code != 200:
        rep.error = f"import failed: {r.text[:200]}"
        return rep
    src = r.json()
    sid = src["id"]
    rep.preflight_ok = bool((src.get("preflight") or {}).get("ok"))
    rep.duration = src.get("duration")
    rep.size = f"{src.get('width')}x{src.get('height')}"
    if not rep.preflight_ok:
        rep.error = f"preflight: {json.dumps((src.get('preflight') or {}).get('issues'), ensure_ascii=False)[:300]}"
        return rep

    print(f"  pipeline (proxy, transcribe, analyze, moments)…", flush=True)
    client.post(f"/sources/{sid}/start", json={})
    run_queue("main")
    src = client.get(f"/sources/{sid}").json()
    rep.status = src.get("status")
    rep.steps = {k: round(v, 2) for k, v in step_times.get(sid, {}).items()}
    rep.step_states = {k: v.get("state") for k, v in (src.get("steps") or {}).items()}
    rep.content_type = src.get("content_type")
    tpath = tasks.transcript_path(sid)
    transcript = json.loads(tpath.read_text("utf-8")) if tpath.exists() else {}
    words = [w for w in transcript.get("words", []) if str(w.get("text", "")).strip()]
    rep.words, rep.language = len(words), transcript.get("language")
    rep.whisper_model, rep.whisper_device = transcript.get("model"), transcript.get("device")
    rep.whisper_timing = transcript.get("timing")

    moments = client.get(f"/sources/{sid}/moments").json() if rep.status != "error" else []
    rep.moments = len(moments)
    if rep.status == "error" or not moments:
        rep.error = rep.error or (json.dumps(src.get("error"), ensure_ascii=False) if rep.status == "error" else None)
        return rep

    best = sorted(moments, key=lambda m: m.get("score") or 0, reverse=True)[: max(0, args.clips)]
    if not best:
        return rep
    print(f"  approve {len(best)} moment(s), prepare + reframe…", flush=True)
    client.post(f"/sources/{sid}/moments/batch", json={"ids": [m["id"] for m in best], "status": "approved"})
    run_queue("edit")
    clips = [c for c in client.get(f"/sources/{sid}/clips").json() if c["moment_id"] in {m["id"] for m in best}]
    for c in clips:
        body = {"renderer": args.renderer} if args.renderer else {}
        client.post(f"/clips/{c['id']}/render", json=body)
    print(f"  render {len(clips)} clip(s)…", flush=True)
    run_queue("render")

    for c in clips:
        clip = clips_service.get_clip(c["id"])
        edl = clip["edl"]
        segs = [{"start": s["start"], "end": s["end"]} for s in edl["segments"]]
        cr = ClipReport(id=c["id"], duration=clip["duration"], segments=segs, crop_mode=(edl.get("crop") or {}).get("mode"))
        rep.clips.append(cr)
        cr.cuts_inside_words, cr.cut_points = cuts_inside_words(segs, words)
        crop = edl.get("crop") or {}
        if crop.get("mode") != "split":
            cr.max_pan, cr.jitter_reversals = pan_profile(crop.get("keyframes") or [], crop.get("easing", "linear"), clip["duration"])

        caps = edl.get("captions") or {}
        cwords = caps.get("words") or []
        # caption words keep the transcript's timestamps (matched by text, nearest start)
        drifts = []
        for w in cwords:
            same = [t for t in words if t["text"].strip() == str(w["text"]).strip()]
            if same:
                drifts.append(min(abs(t["start"] - w["start"]) + abs(t["end"] - w["end"]) for t in same))
        cr.caption_word_drift = round(max(drifts), 3) if drifts else None
        pages = cap.build_pages(cwords, segs, caps.get("words_per_page", 3))
        style = caps.get("style", "bold")
        all_text = " ".join(cap.display_text(cap.page_text(p), style) for p in pages)
        cr.uncovered_chars = uncovered(all_text, cmaps)
        cr.non_nfc_words = sum(1 for w in cwords if unicodedata.normalize("NFC", str(w["text"])) != str(w["text"]))

        out = client.get(f"/clips/{c['id']}/export").json()
        render = out.get("render") or {}
        if render.get("state") != "done":
            cr.error = f"render {render.get('state')}: {json.dumps(render.get('error'), ensure_ascii=False)[:200]}"
            continue
        info = render.get("info") or {}
        cr.renderer, cr.encoder, cr.render_seconds = info.get("renderer"), info.get("encoder"), info.get("render_seconds")
        cr.ssim_min = info.get("ssim_min")
        final = Path(out["out_dir"]) / (info.get("file") or "clip_final.mp4")
        loud = audio_mix.measure(final)  # independent of the render's own measurement
        cr.lufs, cr.true_peak = loud.get("lufs"), loud.get("true_peak")

        srt_file = Path(out["out_dir"]) / "captions.srt"
        if srt_file.exists():
            written = srt_file.read_text("utf-8").replace("\r\n", "\n").strip()
            cr.srt_matches = written == cap.to_srt(pages).replace("\r\n", "\n").strip()
            starts = [float(h) * 3600 + float(m) * 60 + float(s.replace(",", "."))
                      for h, m, s in re.findall(r"^(\d+):(\d+):(\d+,\d+) -->", written, flags=re.M)]
            if starts and len(starts) == len(pages):
                cr.srt_drift = round(max(abs(a - p["start"]) for a, p in zip(starts, pages)), 3)
        if caps.get("enabled", True) and pages:
            cr.onset_offsets = onset_offsets(pages, audio_db(final))
    return rep


# ----------------------------------------------------------------------------- report


def fmt_s(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{int(x // 60)}m {x % 60:04.1f}s" if x >= 60 else f"{x:.1f}s"


def evaluate(samples: list[SampleReport], cmaps: dict[str, set[int]], leftovers: list[str], gpu: bool) -> list[Metric]:
    from pipeline import captions as cap

    m: list[Metric] = []
    clips = [c for s in samples for c in s.clips]
    rendered = [c for c in clips if c.render_seconds is not None]

    # transcription speed, projected to a 30-min video: the decoding part scales with the length,
    # the fixed part (process start, model load) does not
    def projected_30min(s: SampleReport) -> float:
        total = s.steps["transcribe"]
        decode = (s.whisper_timing or {}).get("transcribe_s")
        if not decode or decode > total:
            return total / s.duration * 1800  # no timing: scale everything (conservative)
        return (total - decode) + decode / s.duration * 1800

    speeds = [(projected_30min(s), s) for s in samples if s.duration and "transcribe" in s.steps and s.words]
    if speeds:
        projected, s = max(speeds, key=lambda x: x[0])
        limit = TRANSCRIBE_30MIN_GPU * (1 if gpu else CPU_SLOWDOWN_OK)
        status = "PASS" if projected <= limit else "FAIL"
        timing = s.whisper_timing or {}
        how = (f"decoding {fmt_s(timing['transcribe_s'])} + fixed {fmt_s(s.steps['transcribe'] - timing['transcribe_s'])}"
               f" (model load {fmt_s(timing.get('load_s') or 0)}){', batched' if timing.get('batched') else ''}"
               if timing.get("transcribe_s") else "model load included")
        m.append(Metric("Transcribe speed", status, f"30-min video ≈ {fmt_s(projected)} ({s.whisper_model} on {s.whisper_device})",
                        f"≤ {fmt_s(limit)}" + ("" if gpu else " (CPU: 5× the GPU target)"),
                        f"slowest sample {s.name}: {fmt_s(s.steps['transcribe'])} for {fmt_s(s.duration)}; {how}"))
    else:
        m.append(Metric("Transcribe speed", "N/A", "no sample with speech", "≤ 3m on GPU"))

    if clips:
        inside = sum(c.cuts_inside_words for c in clips)
        total = sum(c.cut_points for c in clips)
        m.append(Metric("Cut points", "PASS" if inside == 0 else "FAIL", f"{inside} of {total} inside a word", "0"))
    if rendered:
        worst = max(rendered, key=lambda c: c.render_seconds / max(c.duration, 0.1))
        ratio = worst.render_seconds / max(worst.duration, 0.1)
        cpu_encoder = worst.encoder not in ("nvenc", "videotoolbox")
        # CLAUDE.md: the numbers are for a mid-range GPU; a CPU run 3-5x slower is acceptable
        status = "PASS" if ratio <= RENDER_RATIO else "WARN" if cpu_encoder and ratio <= RENDER_RATIO * CPU_SLOWDOWN_OK else "FAIL"
        note = f"{worst.renderer} · encoder {worst.encoder}"
        if status == "WARN":
            note += "; slower than the GPU target but within the CPU allowance (no GPU encoder here)"
        m.append(Metric("Render time", status,
                        f"{fmt_s(worst.render_seconds)} for a {fmt_s(worst.duration)} clip (45 s clip ≈ {fmt_s(45 * ratio)})",
                        "45 s clip ≤ 90 s", note))
        lufs = [c.lufs for c in rendered if c.lufs is not None]
        peaks = [c.true_peak for c in rendered if c.true_peak is not None]
        if lufs:
            off = max(lufs, key=lambda v: abs(v - LUFS_TARGET))
            m.append(Metric("Loudness", "PASS" if abs(off - LUFS_TARGET) <= LUFS_TOL else "FAIL",
                            ", ".join(f"{v:.1f}" for v in lufs) + " LUFS", "−14 ± 1 LUFS"))
        if peaks:
            m.append(Metric("True peak", "PASS" if max(peaks) <= TRUE_PEAK_MAX else "FAIL",
                            f"max {max(peaks):.1f} dBTP", "≤ −1 dBTP"))
        ssim = [c.ssim_min for c in rendered if c.ssim_min is not None]
        if ssim:
            m.append(Metric("Preview vs render", "PASS" if min(ssim) >= SSIM_MIN else "FAIL", f"SSIM min {min(ssim):.3f}",
                            "≥ 0.95 (3 frames)", "Remotion stills vs the FFmpeg compositor at the same frame numbers"))
        else:
            m.append(Metric("Preview vs render", "N/A", "no SSIM (Remotion not available)", "≥ 0.95"))
    drift = [c.caption_word_drift for c in clips if c.caption_word_drift is not None]
    srt = [c.srt_drift for c in clips if c.srt_drift is not None]
    if drift or srt:
        worst_drift = max(drift + srt)
        m.append(Metric("Caption timing (data)", "PASS" if worst_drift <= CAPTION_OFFSET_MAX else "FAIL",
                        f"max {worst_drift * 1000:.0f} ms", "≤ 100 ms", "caption words + SRT cues vs transcript word timestamps"))
    onsets = [abs(o) for c in clips for o in c.onset_offsets]
    if len(onsets) >= 3:
        onsets.sort()
        p90 = onsets[min(len(onsets) - 1, int(0.9 * len(onsets)))]
        m.append(Metric("Caption vs speech onset", "PASS" if p90 <= CAPTION_OFFSET_MAX else "WARN",
                        f"median {statistics.median(onsets) * 1000:.0f} ms · p90 {p90 * 1000:.0f} ms · n={len(onsets)}",
                        "≤ 100 ms", "estimate: page start vs audio onset after a pause (whisper word timing)"))
    elif clips:
        m.append(Metric("Caption vs speech onset", "N/A", f"{len(onsets)} clear onsets", "≤ 100 ms", "too few pauses to measure"))

    # Vietnamese diacritics: every letter in every case + a sentence through SRT and ASS
    vi_text = VI_LETTERS + VI_LETTERS.upper() + " " + VI_SENTENCE
    missing = uncovered(vi_text + " " + vi_text.upper(), cmaps)
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "text": w} for i, w in enumerate(VI_SENTENCE.split())]
    edl = {"segments": [{"start": 0, "end": len(words) * 0.5}], "captions": {"words": words, "words_per_page": 3, "style": "bold"}}
    srt_text = "".join(re.findall(r"^[^\d\n].*$", cap.edl_srt(edl), flags=re.M))
    srt_ok = srt_text.replace(" ", "") == VI_SENTENCE.replace(" ", "")
    ass_ok = all(cap.display_text(w["text"], "bold") in cap.to_ass(edl) for w in words)
    clip_missing = "".join(sorted({ch for c in clips for ch in c.uncovered_chars}))
    nfc_bad = sum(c.non_nfc_words for c in clips)
    srt_files_ok = all(c.srt_matches is not False for c in clips)
    ok = not missing and srt_ok and ass_ok and not clip_missing and not nfc_bad and srt_files_ok
    m.append(Metric("Vietnamese diacritics", "PASS" if ok else "FAIL",
                    f"{len(set(vi_text))} letters × {len(cmaps)} caption fonts" + (f" · missing {missing or clip_missing!r}" if missing or clip_missing else ""),
                    "100 % covered, SRT/ASS text unchanged",
                    f"SRT {'ok' if srt_ok else 'changed'} · ASS {'ok' if ass_ok else 'changed'} · non-NFC words {nfc_bad} · "
                    f"written SRT files {'match' if srt_files_ok else 'differ'}"))

    pans = [c for c in clips if c.max_pan is not None]
    if pans:
        worst = max(pans, key=lambda c: c.max_pan)
        jitter = sum(c.jitter_reversals or 0 for c in pans)
        m.append(Metric("Crop pan speed", "PASS" if worst.max_pan <= PAN_MAX + 0.01 else "FAIL",
                        f"max {worst.max_pan * 100:.1f} % of the width per second", "≤ ~25 %/s",
                        f"mode {worst.crop_mode}" + ("; the crop never moved on these samples, so the cap itself is"
                                                     " checked by pytest (test_keyframes.py)" if worst.max_pan == 0 else "")))
        m.append(Metric("Crop jitter", "PASS" if jitter == 0 else "FAIL", f"{jitter} small back-and-forth reversals", "0"))

    ok, detail = locale_parity()
    m.append(Metric("i18n key parity", "PASS" if ok else "FAIL", detail, "same keys, no empty values, same {{placeholders}}"))
    m.append(Metric("Leftover temp files", "PASS" if not leftovers else "FAIL", f"{len(leftovers)}", "0",
                    ", ".join(leftovers[:5])))
    m.append(Metric("Preview response", "N/A", "needs a browser", "≤ 200 ms", "checked by hand / UI smoke (frontend/scripts/smoke.mjs)"))
    m.append(Metric("Resume after close", "N/A", "covered by pytest", "resume unfinished step",
                    "tests/test_jobs.py::test_recover_requeues_running_jobs_and_removes_temp_files, "
                    "tests/test_api_sources.py::test_resume_after_restart_skips_finished_steps"))
    return m


def print_report(samples: list[SampleReport], metrics: list[Metric], env: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(f"ShortForge e2e report · {env['time']}")
    print(f"device {env['device']} · ffmpeg encoders {', '.join(env['encoders'])} · Remotion {'yes' if env['remotion'] else 'no'}")
    for s in samples:
        print(f"\n{s.name}  ({fmt_s(s.duration)}, {s.size}, {s.content_type or '?'}, {s.language or '?'}, {s.words} words)")
        if s.steps:
            print("  steps: " + " · ".join(f"{k} {fmt_s(v)}" for k, v in s.steps.items()))
        print(f"  moments proposed: {s.moments} · clips rendered: {sum(1 for c in s.clips if c.render_seconds is not None)}")
        for c in s.clips:
            line = (f"  {c.id}: {fmt_s(c.duration)} · crop {c.crop_mode} · {c.renderer or '—'}/{c.encoder or '—'} "
                    f"in {fmt_s(c.render_seconds)} · {c.lufs if c.lufs is None else round(c.lufs, 1)} LUFS · "
                    f"TP {c.true_peak if c.true_peak is None else round(c.true_peak, 1)} · SSIM {c.ssim_min}")
            print(line + (f" · ERROR {c.error}" if c.error else ""))
        if s.error:
            print(f"  note: {s.error}")
    print("\nAcceptance")
    w = max(len(x.name) for x in metrics)
    for x in metrics:
        print(f"  [{x.status:<4}] {x.name:<{w}}  {x.value}  (target {x.target})")
        if x.note:
            print(f"  {'':<6} {'':<{w}}  {x.note}")
    fails = sum(1 for x in metrics if x.status == "FAIL")
    warns = sum(1 for x in metrics if x.status == "WARN")
    print(f"\n{len(metrics) - fails - warns} ok · {warns} warning(s) · {fails} failed")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252 (no Vietnamese, no ≤)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = setup_env(args, stamp)
    videos = sorted(p for p in args.samples.glob("*") if p.suffix.lower() in VIDEO_EXT and (not args.only or args.only in p.name))
    if not videos:
        print(f"No videos in {args.samples} (see samples/README.md).")
        return 1

    from fastapi.testclient import TestClient

    from app import db, tasks
    from app.config import get_settings
    from app.main import app
    from pipeline import render

    step_times: dict[str, dict[str, float]] = {}

    def timed(name: str, fn: Any) -> Any:
        def run(ctx: Any, src: Any, report: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return fn(ctx, src, report)
            finally:
                step_times.setdefault(src.id, {})[name] = time.perf_counter() - t0
        return run

    for key, fn in list(tasks.STEP_FUNCS.items()):
        tasks.STEP_FUNCS[key] = timed(key, fn)

    cmaps = font_cmaps()
    samples: list[SampleReport] = []
    print(f"e2e run in {run_dir}")
    with TestClient(app) as client:
        health = client.get("/health").json()
        for video in videos:
            print(f"\n▶ {video.name}", flush=True)
            t0 = time.perf_counter()
            try:
                samples.append(run_sample(client, video, args, step_times, cmaps))
            except Exception as exc:  # keep going with the other samples, report the failure
                samples.append(SampleReport(name=video.name, error=f"{type(exc).__name__}: {exc}"))
            print(f"  done in {fmt_s(time.perf_counter() - t0)}", flush=True)
    leftovers = leftover_temp_files(run_dir)
    db.dispose()

    gpu_name = (health.get("gpu") or {}).get("name")
    device = f"{health.get('device', '?')}" + (f" ({gpu_name})" if gpu_name else "")
    gpu = health.get("device") in ("cuda", "mps")
    env = {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "device": device,
           "encoders": render.detect_encoders(get_settings().ffmpeg_bin), "remotion": render.remotion_available()}
    metrics = evaluate(samples, cmaps, leftovers, gpu)
    print_report(samples, metrics, env)

    report = ROOT / "data" / "e2e" / f"report_{stamp}.json"
    report.write_text(json.dumps({"env": env, "samples": [asdict(s) for s in samples], "metrics": [asdict(x) for x in metrics]},
                                 ensure_ascii=False, indent=2), "utf-8")
    print(f"report saved: {report.relative_to(ROOT)}")
    if not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    return 1 if any(x.status == "FAIL" for x in metrics) else 0


if __name__ == "__main__":
    sys.exit(main())
