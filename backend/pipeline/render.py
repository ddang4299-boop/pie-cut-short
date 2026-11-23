"""Step 11 - Render a clip's EDL to a 1080x1920 MP4.

Two renderers share the audio chain (pipeline/audio_mix.py) and the final encoder:

- **remotion** (default): `remotion/scripts/render.mjs` renders the "Short" composition muted (the
  same component as the editor preview, so preview == render), then FFmpeg muxes the mixed audio
  and encodes with the platform preset.
- **ffmpeg** (fallback, or chosen in Settings): source frames are decoded by FFmpeg, framed in
  Python (pipeline/compose.py mirrors VideoLayer.tsx), the progress bar is drawn, and FFmpeg burns
  the ASS captions (libass + bundled Be Vietnam Pro) while encoding.

If Remotion fails, the FFmpeg path runs automatically. The encoder is h264_nvenc on NVIDIA,
h264_videotoolbox on macOS, otherwise libx264; when a GPU encoder fails the encode is retried
with libx264.

Verification: Remotion stills (overlays off) at 25/50/75 % of the clip are compared with the same
frames framed by the Python compositor (SSIM, must be >= 0.95), and the final loudness is
measured (EBU R128).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pipeline import audio_mix, compose
from pipeline.ingest import FFmpegError, run_ffmpeg, safe_unlink
from pipeline.keyframes import clip_duration

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTION_DIR = REPO_ROOT / "remotion"
FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
SSIM_MIN = 0.95
VERIFY_POINTS = (0.25, 0.5, 0.75)
REMOTION_CRF = 16  # intermediate (re-encoded by the final encoder)

ProgressFn = Callable[[float, str], None]
LogFn = Callable[[str], None]
ProcessFn = Callable[[subprocess.Popen], None]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RenderError(RuntimeError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail or message


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------
# encoders
# --------------------------------------------------------------------------
_ENCODERS: dict[str, list[str]] = {}
_ENC_LOCK = threading.Lock()
GPU_ENCODERS = ("h264_nvenc", "h264_videotoolbox")


def detect_encoders(ffmpeg: str = "ffmpeg") -> list[str]:
    """Usable H.264 encoders, best first (always ends with libx264). Each candidate is tried with
    a tiny test encode, since listing an encoder does not mean the GPU/driver supports it."""
    with _ENC_LOCK:
        if ffmpeg in _ENCODERS:
            return list(_ENCODERS[ffmpeg])
        candidates = ["h264_nvenc"] + (["h264_videotoolbox"] if sys.platform == "darwin" else [])
        found = [enc for enc in candidates if _try_encoder(ffmpeg, enc)] + ["libx264"]
        _ENCODERS[ffmpeg] = found
        return list(found)


def _try_encoder(ffmpeg: str, encoder: str) -> bool:
    try:
        r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                            "-c:v", encoder, "-pix_fmt", "yuv420p", "-f", "null", "-"],
                           capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def encoder_label(encoder: str) -> str:
    return {"h264_nvenc": "nvenc", "h264_videotoolbox": "videotoolbox"}.get(encoder, "cpu")


def video_args(encoder: str, preset: dict[str, Any], fps: float) -> list[str]:
    """Video encoder arguments for a platform preset (target bitrate, high profile, 2 s GOP)."""
    b = float(preset["video_bitrate_mbps"])
    gop = str(int(round(fps * 2)))
    if encoder == "h264_nvenc":
        args = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-b:v", f"{b:g}M",
                "-maxrate", f"{b * 1.5:g}M", "-bufsize", f"{b * 2:g}M", "-profile:v", "high", "-bf", "2"]
    elif encoder == "h264_videotoolbox":
        args = ["-c:v", "h264_videotoolbox", "-b:v", f"{b:g}M", "-maxrate", f"{b * 1.5:g}M",
                "-bufsize", f"{b * 2:g}M", "-profile:v", "high"]
    else:
        args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", f"{b:g}M",
                "-bufsize", f"{b * 2:g}M", "-profile:v", "high"]
    return args + ["-g", gop, "-pix_fmt", "yuv420p", "-r", f"{fps:g}"] + COLOR_TAGS


# every output is BT.709 limited range and says so (Remotion's frames arrive as full-range
# JPEG/BT.601; untagged or full-range files show shifted colors on phones)
COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
# setparams: FFmpeg 7+ takes primaries/transfer from the frames, not from -color_* (left "unknown")
TO_BT709 = ("scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")


def audio_args(preset: dict[str, Any]) -> list[str]:
    return ["-c:a", "aac", "-b:a", f"{int(preset['audio_bitrate_kbps'])}k", "-ar", "48000", "-ac", "2"]


# --------------------------------------------------------------------------
# remotion availability
# --------------------------------------------------------------------------
def node_bin() -> str | None:
    return shutil.which("node")


def remotion_available() -> bool:
    return bool(node_bin()) and (REMOTION_DIR / "node_modules" / "@remotion" / "renderer").exists() \
        and (REMOTION_DIR / "scripts" / "render.mjs").exists()


def browser_executable() -> str | None:
    """None lets Remotion use its Chrome Headless Shell (remotion/node_modules/.remotion, fastest).
    When that was never downloaded, use an installed Chromium browser instead of downloading."""
    if list((REMOTION_DIR / "node_modules" / ".remotion").glob("chrome-headless-shell/*/*/chrome-headless-shell*")):
        return None
    for cand in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if Path(cand).exists():
            return cand
    return None


# --------------------------------------------------------------------------
# render source
# --------------------------------------------------------------------------
RENDER_SRC_PAD = 0.5


def source_color_space(media: Path, ffprobe: str = "ffprobe") -> str | None:
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space",
                            "-of", "default=nw=1:nk=1", str(media)], capture_output=True, text=True, timeout=30,
                           creationflags=_NO_WINDOW)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def render_source(media: Path, work_dir: Path, *, start: float, end: float, fps: float, has_audio: bool,
                  ffmpeg: str = "ffmpeg", log: LogFn | None = None, on_process: ProcessFn | None = None,
                  on_progress: Callable[[float], None] | None = None) -> tuple[Path, float]:
    """Frame-accurate intermediate of [start - pad, end + pad]: CFR, short GOP, timestamps from 0.

    Both renderers read this file, so they see the same frame at the same time (a stream-copied
    rough cut starts on a keyframe and its first video timestamp is often not 0, which made the two
    renderers pick different frames). Returns (path, source time of its t=0). Cached."""
    lo = max(0.0, round(start - RENDER_SRC_PAD, 3))
    hi = round(end + RENDER_SRC_PAD, 3)
    out = work_dir / "render_src.mp4"
    meta_path = work_dir / "render_src.json"
    st = media.stat()
    key = {"media": str(media), "size": st.st_size, "mtime": int(st.st_mtime), "lo": lo, "hi": hi, "fps": fps}
    if out.exists() and meta_path.exists():
        try:
            if json.loads(meta_path.read_text("utf-8")) == key:
                return out, lo
        except ValueError:
            pass
    part = work_dir / "render_src.mp4.part"
    cmd = [ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-ss", f"{lo:.3f}",
           "-i", str(media), "-t", f"{hi - lo:.3f}", "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "pcm_s16le", "-ar", "48000"]
    if source_color_space(media) in (None, "unknown"):
        cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    cmd += ["-fps_mode", "cfr", "-r", f"{fps:g}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
            "-g", str(int(round(fps / 2)) or 1), "-bf", "0", "-pix_fmt", "yuv420p",
            "-avoid_negative_ts", "make_zero", "-f", "mov", str(part)]
    try:
        run_ffmpeg(cmd, duration=hi - lo, on_progress=on_progress, on_process=on_process, log=log)
        os.replace(part, out)
    finally:
        safe_unlink(part)
    meta_path.write_text(json.dumps(key), "utf-8")
    return out, lo


# --------------------------------------------------------------------------
# request
# --------------------------------------------------------------------------
@dataclass
class RenderInput:
    edl: dict[str, Any]
    media: Path  # video file (usually the clip's rough cut)
    media_offset: float  # source time of media time 0
    src_w: int
    src_h: int
    has_audio: bool
    language: str
    preset: dict[str, Any]
    work_dir: Path
    out_dir: Path
    renderer: str = "remotion"
    isolated: tuple[Path, float] | None = None
    voiceover: Path | None = None
    vo_duration: float | None = None
    music: tuple[Path, float] | None = None  # (library track, its duration)
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    data_dir: Path | None = None
    verify: bool = True


@dataclass
class Hooks:
    progress: ProgressFn = lambda f, step: None
    log: LogFn = lambda msg: None
    on_process: ProcessFn = lambda p: None
    cancelled: Callable[[], bool] = lambda: False

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled()


@dataclass
class RenderResult:
    final: Path
    renderer: str
    encoder: str
    duration: float
    width: int
    height: int
    fps: float
    lufs: float | None = None
    true_peak: float | None = None
    ssim: list[float] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.final.name, "renderer": self.renderer, "encoder": encoder_label(self.encoder),
            "encoder_name": self.encoder, "duration": round(self.duration, 3), "width": self.width,
            "height": self.height, "fps": self.fps, "lufs": self.lufs, "true_peak": self.true_peak,
            "ssim": self.ssim, "ssim_min": min(self.ssim) if self.ssim else None, "warnings": self.warnings,
            "render_seconds": round(self.seconds, 1),
        }


def local_edl(edl: dict[str, Any], offset: float) -> dict[str, Any]:
    """The EDL with segments moved to media time (media starts at source time `offset`)."""
    out = dict(edl)
    out["segments"] = [{"start": round(s["start"] - offset, 4), "end": round(s["end"] - offset, 4)}
                       for s in edl.get("segments") or []]
    return out


def without_overlays(edl: dict[str, Any]) -> dict[str, Any]:
    out = dict(edl)
    out["captions"] = {**(edl.get("captions") or {}), "enabled": False}
    out["hook_text"] = {**(edl.get("hook_text") or {}), "enabled": False}
    out["progress_bar"] = {**(edl.get("progress_bar") or {}), "enabled": False}
    return out


def output_fps(edl: dict[str, Any], preset: dict[str, Any]) -> float:
    return float(preset.get("fps") or (edl.get("output") or {}).get("fps", 30))


def composition_props(edl: dict[str, Any], inp: RenderInput, fps: float) -> dict[str, Any]:
    """Props of the Remotion "Short" composition (schema: remotion/src/schema.ts)."""
    out = dict(edl.get("output") or {"w": 1080, "h": 1920})
    out["fps"] = fps
    keep = ("segments", "crop", "captions", "hook_text", "progress_bar")
    e = {k: edl[k] for k in keep if k in edl}
    e["audio"] = {"volume_db": float((edl.get("audio") or {}).get("volume_db", 0.0))}
    e["output"] = out
    return {"edl": e, "video": {"src": None, "width": inp.src_w, "height": inp.src_h},
            "language": inp.language or "en", "showVideo": True}


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------
def render_audio(inp: RenderInput, hooks: Hooks, *, include_voiceover: bool = True, include_music: bool = True,
                 name: str = "mix") -> Path:
    """Mixed + loudness-normalized WAV of the clip (`name`.wav in the work dir)."""
    plan = audio_mix.build_plan(
        inp.edl, media=inp.media if inp.has_audio else None, media_offset=inp.media_offset,
        isolated=inp.isolated, voiceover=inp.voiceover, vo_duration=inp.vo_duration,
        include_voiceover=include_voiceover, music=inp.music, include_music=include_music)
    raw = inp.work_dir / f"{name}_raw.wav"
    out = inp.work_dir / f"{name}.wav"
    audio_mix.render_mix(plan, raw, ffmpeg=inp.ffmpeg, log=hooks.log, on_process=hooks.on_process)
    hooks.check()
    target = float((inp.edl.get("audio") or {}).get("normalize_lufs", -14.0))
    audio_mix.normalize(raw, out, target=target, ffmpeg=inp.ffmpeg, log=hooks.log, on_process=hooks.on_process)
    safe_unlink(raw)
    return out


# --------------------------------------------------------------------------
# remotion
# --------------------------------------------------------------------------
def run_remotion(request: dict[str, Any], work_dir: Path, hooks: Hooks, on_frac: Callable[[float], None]) -> None:
    """Run render.mjs with a request; raises RenderError with the script's message on failure."""
    req_path = work_dir / "remotion_request.json"
    req_path.write_text(json.dumps(request), encoding="utf-8")
    cmd = [node_bin() or "node", str(REMOTION_DIR / "scripts" / "render.mjs"), str(req_path)]
    hooks.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=REMOTION_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    hooks.on_process(proc)
    err_tail: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_tail.append(line.rstrip())
            del err_tail[:-30]

    threading.Thread(target=drain, daemon=True).start()
    message = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("event")
        if kind == "bundle":
            on_frac(0.05 * float(ev.get("progress", 0)))
        elif kind == "progress":
            on_frac(0.05 + 0.95 * float(ev.get("progress", 0)))
        elif kind == "composition":
            hooks.log(f"remotion composition: {ev}")
        elif kind == "error":
            message = str(ev.get("message", ""))
        if hooks.cancelled():
            break
    proc.wait()
    hooks.check()
    if proc.returncode != 0:
        detail = (message or "\n".join(err_tail[-10:]) or f"exit code {proc.returncode}")
        hooks.log("remotion failed: " + detail)
        raise RenderError("remotion render failed", detail.splitlines()[0][:300] if detail else "")


def remotion_request(inp: RenderInput, edl: dict[str, Any], fps: float, *, output: Path | None,
                     stills: list[tuple[int, Path]]) -> dict[str, Any]:
    props = composition_props(edl, inp, fps)
    clean = composition_props(without_overlays(edl), inp, fps)
    cache = (inp.data_dir or inp.work_dir) / "cache" / "remotion_bundle"
    return {
        "entry": str(REMOTION_DIR / "src" / "index.ts"),
        "bundleCache": str(cache),
        "media": str(inp.media),
        "props": props,
        "output": str(output) if output else None,
        "stills": [{"frame": f, "output": str(p), "props": json.loads(json.dumps(clean))} for f, p in stills],
        "browser": browser_executable(),
        "concurrency": max(2, min(12, (os.cpu_count() or 4) * 3 // 4)),
        "crf": REMOTION_CRF,
        "x264Preset": "veryfast",
    }


# --------------------------------------------------------------------------
# ffmpeg frame pipeline
# --------------------------------------------------------------------------
def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def prepare_fonts(work_dir: Path) -> Path:
    """Copy the caption fonts next to the ASS file (relative fontsdir avoids Windows path escaping)."""
    dst = work_dir / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob("*.ttf"):
        target = dst / f.name
        if not target.exists():
            shutil.copy2(f, target)
    return dst


def _decoder(inp: RenderInput, start: float, count: int, fps: float) -> subprocess.Popen:
    # a quarter frame early: the first frame kept is exactly the wanted one (no float edge), and it
    # lands 1/4 frame after 0, so the fps filter never sees a rounding tie
    cmd = [inp.ffmpeg, "-v", "error", "-nostdin", "-ss", f"{max(0.0, start - 0.25 / fps):.4f}", "-i", str(inp.media),
           "-map", "0:v:0", "-vf", f"fps={fps:g}", "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                            bufsize=inp.src_w * inp.src_h * 3)


def read_frame(proc: subprocess.Popen, size: int) -> bytes | None:
    assert proc.stdout is not None
    buf = proc.stdout.read(size)
    return buf if buf and len(buf) == size else None


def render_frames(inp: RenderInput, edl: dict[str, Any], out: Path, *, audio: Path, encoder: str, fps: float,
                  overlays: bool, hooks: Hooks, on_frac: Callable[[float], None]) -> None:
    """FFmpeg + Python render of `edl` (segments in media time) to `out`."""
    from pipeline.captions import to_ass

    W, H = int(edl["output"]["w"]), int(edl["output"]["h"])
    segments = edl["segments"]
    plan = compose.frame_plan(segments, fps)
    total = compose.total_frames(segments, fps)
    vf = TO_BT709
    if overlays:
        prepare_fonts(inp.work_dir)
        (inp.work_dir / "captions.ass").write_text(to_ass({**edl, "output": {**edl["output"], "fps": fps}}), encoding="utf-8")
        vf = "ass=captions.ass:fontsdir=fonts," + vf
    cmd = [inp.ffmpeg, "-y", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", f"{fps:g}", "-i", "pipe:0", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-vf", vf]
    cmd += video_args(encoder, inp.preset, fps) + audio_args(inp.preset)
    cmd += ["-shortest", "-movflags", "+faststart", "-f", "mp4", str(out)]
    hooks.log("$ " + " ".join(cmd))
    enc = subprocess.Popen(cmd, cwd=inp.work_dir, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                           creationflags=_NO_WINDOW)
    hooks.on_process(enc)
    err: list[bytes] = []
    t = threading.Thread(target=lambda: err.append(enc.stderr.read() if enc.stderr else b""), daemon=True)
    t.start()
    frame_bytes = inp.src_w * inp.src_h * 3
    color = (edl.get("progress_bar") or {}).get("color", "#FFD400")
    bar = overlays and (edl.get("progress_bar") or {}).get("enabled", True)
    written = 0
    src = np.zeros((inp.src_h, inp.src_w, 3), dtype=np.uint8)
    try:
        assert enc.stdin is not None
        for sf in plan:
            dec = _decoder(inp, sf.source_start, sf.count, fps)
            hooks.on_process(dec)
            try:
                for k in range(sf.count):
                    frame_no = sf.first_frame + k
                    if frame_no >= total:
                        break
                    buf = read_frame(dec, frame_bytes)
                    if buf is not None:
                        src = np.frombuffer(buf, dtype=np.uint8).reshape(inp.src_h, inp.src_w, 3)
                    canvas = compose.compose(src, compose.layout(edl, inp.src_w, inp.src_h, frame_no / fps), W, H)
                    if bar:
                        compose.draw_progress(canvas, min(1.0, (frame_no + 1) / total), color)
                    enc.stdin.write(canvas.tobytes())
                    written += 1
                    if written % 15 == 0:
                        on_frac(written / total)
                        hooks.check()
            finally:
                if dec.poll() is None:
                    dec.kill()
                dec.wait()
        enc.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        enc.wait()
        t.join(timeout=5)
        hooks.check()
        tail = b"".join(err).decode("utf-8", "replace")[-1500:]
        raise FFmpegError(f"encoder stopped: {exc}", tail) from exc
    except Cancelled:
        enc.kill()
        enc.wait()
        raise
    enc.wait()
    t.join(timeout=5)
    tail = b"".join(err).decode("utf-8", "replace")[-1500:]
    if tail.strip():
        hooks.log(tail)
    if enc.returncode != 0:
        raise FFmpegError(f"ffmpeg exited with code {enc.returncode}", tail)
    on_frac(1.0)


def mux_encode(inp: RenderInput, video: Path, audio: Path, out: Path, *, encoder: str, fps: float,
               duration: float, hooks: Hooks, on_frac: Callable[[float], None]) -> None:
    cmd = [inp.ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(video),
           "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-vf", TO_BT709]
    cmd += video_args(encoder, inp.preset, fps) + audio_args(inp.preset)
    cmd += ["-shortest", "-movflags", "+faststart", "-f", "mp4", str(out)]
    run_ffmpeg(cmd, duration=duration, on_progress=on_frac, on_process=hooks.on_process, log=hooks.log)


def with_encoder_fallback(encoders: list[str], run: Callable[[str], None], result: RenderResult, hooks: Hooks) -> str:
    """Run an encode with the best encoder, retrying with libx264 when a GPU encoder fails."""
    last: Exception | None = None
    for enc in encoders:
        try:
            run(enc)
            return enc
        except FFmpegError as exc:
            hooks.check()
            last = exc
            hooks.log(f"encoder {enc} failed: {exc} {exc.stderr_tail[-500:]}")
            if enc != "libx264":
                result.warnings.append({"code": "RENDER_ENCODER_FALLBACK", "params": {"encoder": encoder_label(enc)}})
    raise RenderError("encode failed", str(getattr(last, "stderr_tail", "") or last)[-300:])


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def verify_frames(total: int) -> list[int]:
    return sorted({min(total - 1, max(0, int(round(total * p)))) for p in VERIFY_POINTS})


def source_frame(inp: RenderInput, t: float, fps: float = 30.0) -> np.ndarray | None:
    r = subprocess.run([inp.ffmpeg, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t - 0.25 / fps):.4f}", "-i", str(inp.media),
                        "-map", "0:v:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                       capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    size = inp.src_w * inp.src_h * 3
    if r.returncode != 0 or len(r.stdout) < size:
        return None
    return np.frombuffer(r.stdout[:size], dtype=np.uint8).reshape(inp.src_h, inp.src_w, 3)


def ssim(a: Path, b: Path, ffmpeg: str = "ffmpeg") -> float | None:
    r = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi",
                        "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]ssim", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                       creationflags=_NO_WINDOW)
    m = re.search(r"All:([\d.]+)", r.stderr)
    return float(m.group(1)) if m else None


def compare_stills(inp: RenderInput, edl: dict[str, Any], fps: float, stills: list[tuple[int, Path]]) -> list[float]:
    """SSIM of each Remotion still (preview) vs the Python compositor's frame (FFmpeg path)."""
    import cv2

    W, H = int(edl["output"]["w"]), int(edl["output"]["h"])
    scores = []
    for frame, still in stills:
        if not still.exists():
            continue
        t = compose.source_time_of_frame(edl["segments"], fps, frame)
        src = source_frame(inp, t, fps)
        if src is None:
            continue
        mine = compose.compose(src, compose.layout(edl, inp.src_w, inp.src_h, frame / fps), W, H)
        path = still.with_name(still.stem + "_ffmpeg.png")
        cv2.imwrite(str(path), mine)
        score = ssim(still, path, inp.ffmpeg)
        if score is not None:
            scores.append(round(score, 4))
    return scores


def thumbnail(ffmpeg: str, video: Path, out: Path, at: float) -> None:
    subprocess.run([ffmpeg, "-y", "-v", "error", "-nostdin", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=360:-2", "-q:v", "4", str(out)], capture_output=True, timeout=60,
                   creationflags=_NO_WINDOW)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
STEP_WEIGHTS = {"audio": 0.1, "video": 0.75, "encode": 0.1, "verify": 0.05}


def render_clip(inp: RenderInput, hooks: Hooks | None = None) -> RenderResult:
    """Render the clip to inp.out_dir/clip_final.mp4 (written as .part, then renamed)."""
    hooks = hooks or Hooks()
    t0 = time.monotonic()
    inp.work_dir.mkdir(parents=True, exist_ok=True)
    inp.out_dir.mkdir(parents=True, exist_ok=True)
    edl = local_edl(inp.edl, inp.media_offset)
    fps = output_fps(inp.edl, inp.preset)
    edl["output"] = {**(edl.get("output") or {"w": 1080, "h": 1920}), "fps": fps}
    duration = clip_duration(edl["segments"])
    total = compose.total_frames(edl["segments"], fps)
    encoders = detect_encoders(inp.ffmpeg)
    result = RenderResult(final=inp.out_dir / "clip_final.mp4", renderer=inp.renderer, encoder=encoders[0],
                          duration=duration, width=int(edl["output"]["w"]), height=int(edl["output"]["h"]), fps=fps)
    base = 0.0

    def stage(name: str) -> Callable[[float], None]:
        start, w = base, STEP_WEIGHTS[name]
        return lambda f: hooks.progress(start + w * max(0.0, min(1.0, f)), name)

    hooks.progress(0.0, "audio")
    audio = render_audio(inp, hooks)
    base += STEP_WEIGHTS["audio"]
    hooks.check()

    part = inp.out_dir / "clip_final.mp4.part"
    stills = [(f, inp.work_dir / f"verify_{f}.png") for f in verify_frames(total)] if inp.verify else []
    stills_done = False
    if inp.renderer == "remotion":
        if not remotion_available():
            result.warnings.append({"code": "RENDER_REMOTION_FAILED", "params": {"detail": "Remotion is not installed"}})
            result.renderer = "ffmpeg"
        else:
            muted = inp.work_dir / "remotion.mp4"
            try:
                try:
                    run_remotion(remotion_request(inp, edl, fps, output=muted, stills=stills), inp.work_dir, hooks,
                                 stage("video"))
                except RenderError as exc:  # -> automatic switch to the FFmpeg + ASS path
                    result.warnings.append({"code": "RENDER_REMOTION_FAILED", "params": {"detail": exc.detail[:200]}})
                    result.renderer = "ffmpeg"
                else:
                    stills_done = True
                    base += STEP_WEIGHTS["video"]
                    enc_stage = stage("encode")
                    result.encoder = with_encoder_fallback(
                        encoders, lambda enc: mux_encode(inp, muted, audio, part, encoder=enc, fps=fps,
                                                         duration=duration, hooks=hooks, on_frac=enc_stage),
                        result, hooks)
                    base += STEP_WEIGHTS["encode"]
            finally:
                safe_unlink(muted)
    if result.renderer == "ffmpeg":
        span = STEP_WEIGHTS["video"] + STEP_WEIGHTS["encode"]
        start = base

        def frames(enc: str) -> None:
            render_frames(inp, edl, part, audio=audio, encoder=enc, fps=fps, overlays=True, hooks=hooks,
                          on_frac=lambda f: hooks.progress(start + span * f, "video"))

        result.encoder = with_encoder_fallback(encoders, frames, result, hooks)
        base += span
    hooks.check()

    hooks.progress(base, "verify")
    if inp.verify:
        if not stills_done and remotion_available():
            try:
                run_remotion(remotion_request(inp, edl, fps, output=None, stills=stills), inp.work_dir, hooks, lambda f: None)
                stills_done = True
            except RenderError as exc:
                hooks.log(f"verification stills failed: {exc.detail}")
        if stills_done:
            result.ssim = compare_stills(inp, edl, fps, stills)
            hooks.log(f"ssim (preview vs ffmpeg framing): {result.ssim}")
            if result.ssim and min(result.ssim) < SSIM_MIN:
                result.warnings.append({"code": "RENDER_SSIM_LOW", "params": {"ssim": min(result.ssim)}})
    loud = audio_mix.measure(part, ffmpeg=inp.ffmpeg, on_process=hooks.on_process)
    result.lufs, result.true_peak = loud["lufs"], loud["true_peak"]
    target = float((inp.edl.get("audio") or {}).get("normalize_lufs", -14.0))
    if result.lufs is not None and abs(result.lufs - target) > 1.0:
        result.warnings.append({"code": "RENDER_LOUDNESS_OFF", "params": {"lufs": round(result.lufs, 1), "target": target}})
    hooks.check()
    os.replace(part, result.final)
    thumbnail(inp.ffmpeg, result.final, inp.out_dir / "thumb.jpg", min(1.0, duration / 2))
    result.seconds = time.monotonic() - t0
    hooks.progress(1.0, "verify")
    return result


def render_clean(inp: RenderInput, out: Path, audio: Path, hooks: Hooks | None = None,
                 on_frac: Callable[[float], None] | None = None) -> str:
    """Cropped video without captions/hook/progress bar (CapCut bundle) via the FFmpeg path."""
    hooks = hooks or Hooks()
    edl = local_edl(inp.edl, inp.media_offset)
    fps = output_fps(inp.edl, inp.preset)
    edl["output"] = {**(edl.get("output") or {"w": 1080, "h": 1920}), "fps": fps}
    part = out.with_name(out.name + ".part")
    dummy = RenderResult(final=out, renderer="ffmpeg", encoder="", duration=0, width=0, height=0, fps=fps)
    enc = with_encoder_fallback(detect_encoders(inp.ffmpeg), lambda e: render_frames(
        inp, without_overlays(edl), part, audio=audio, encoder=e, fps=fps, overlays=False, hooks=hooks,
        on_frac=on_frac or (lambda f: None)), dummy, hooks)
    os.replace(part, out)
    return enc
