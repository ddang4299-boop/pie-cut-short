"""Step 1 - Ingest: file or URL (yt-dlp), ffprobe metadata, 720p CFR proxy, thumbnail, audio.

Pure helpers (`quick_hash`, `proxy_size`, `proxy_fps`, `build_proxy_cmd`, `parse_progress_line`)
are unit-tested; the rest wraps ffmpeg / yt-dlp subprocess work.

CLI (run from backend/):
    python -m pipeline.ingest proxy <video> <out.mp4> [--encoder libx264]
    python -m pipeline.ingest download <url> <out_dir>
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

ProgressFn = Callable[[float], None]  # 0..1
LogFn = Callable[[str], None]
ProcessFn = Callable[[subprocess.Popen], None]  # lets the job queue kill it on cancel

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts", ".ts", ".flv", ".wmv", ".mpg", ".mpeg", ".3gp"}
PROXY_SHORT_SIDE = 720
PROXY_MAX_FPS = 30.0
HASH_CHUNK = 1024 * 1024  # 1 MiB from start, middle and end
HASH_VERSION = "q1"


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr_tail: str = ""):
        super().__init__(message)
        self.stderr_tail = stderr_tail


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def safe_unlink(path: Path, attempts: int = 5) -> None:
    """Delete a file, retrying briefly (Windows keeps files locked until a killed process exits)."""
    import time

    for i in range(attempts):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.2 * (i + 1))


def is_video_name(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def quick_hash(path: Path) -> str:
    """Fast content hash: size + 1 MiB from the start, middle and end (sha256).

    Hashing a multi-GB file fully takes seconds; this is enough to recognise the same file
    for caching (proxy, transcript) on one machine.
    """
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha256(f"{HASH_VERSION}:{size}:".encode())
    with path.open("rb") as f:
        offsets = sorted({0, max(0, size // 2 - HASH_CHUNK // 2), max(0, size - HASH_CHUNK)})
        for offset in offsets:
            f.seek(offset)
            h.update(f.read(HASH_CHUNK))
    return h.hexdigest()[:32]


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def proxy_size(width: int, height: int, short_side: int = PROXY_SHORT_SIDE) -> tuple[int, int]:
    """Scale so the short side is `short_side` (never upscale), both dimensions even."""
    if width <= 0 or height <= 0:
        raise ValueError("invalid size")
    shortest = min(width, height)
    if shortest <= short_side:
        return _even(width), _even(height)
    scale = short_side / shortest
    return _even(width * scale), _even(height * scale)


def proxy_fps(fps: float, max_fps: float = PROXY_MAX_FPS) -> str:
    """Constant frame rate for the proxy as an ffmpeg rate string.

    Keeps common NTSC/PAL rates exact (23.976 -> 24000/1001); anything above `max_fps`
    (e.g. 60 fps gameplay) is halved down to <= max_fps; unknown -> 30.
    """
    if not fps or fps <= 0 or fps > 1000:
        return "30"
    while fps > max_fps + 0.01:
        fps /= 2
    for nominal in (24, 30, 60):
        ntsc = nominal * 1000 / 1001
        if abs(fps - ntsc) < 0.01:
            return f"{nominal * 1000}/1001"
    rounded = round(fps)
    if abs(fps - rounded) < 0.01:
        return str(rounded)
    frac = Fraction(fps).limit_denominator(1001)
    return f"{frac.numerator}/{frac.denominator}"


def rate_to_float(rate: str) -> float:
    return float(Fraction(rate))


def build_proxy_cmd(
    src: Path,
    dst: Path,
    *,
    width: int,
    height: int,
    fps: str,
    encoder: str = "libx264",
    has_audio: bool = True,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """ffmpeg command for the preview proxy: H.264, CFR, 1 keyframe per second (fast seeking)."""
    gop = max(1, round(rate_to_float(fps)))
    cmd = [ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src)]
    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0"]
    cmd += ["-vf", f"scale={width}:{height}:flags=bicubic,format=yuv420p", "-fps_mode", "cfr", "-r", fps]
    if encoder == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "26", "-b:v", "0"]
    elif encoder == "h264_videotoolbox":
        cmd += ["-c:v", "h264_videotoolbox", "-q:v", "60"]
    elif encoder == "libx264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]
    else:
        cmd += ["-c:v", encoder]
    cmd += ["-g", str(gop), "-keyint_min", str(gop), "-bf", "0"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", str(dst)]
    return cmd


_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$")


def parse_progress_line(line: str) -> float | None:
    """Seconds of output written, from an `ffmpeg -progress` line (None if not a time line)."""
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):  # both are microseconds in ffmpeg
        try:
            return max(0.0, int(value) / 1_000_000)
        except ValueError:
            return None
    if key == "out_time":
        m = _TIME_RE.match(value)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


# --------------------------------------------------------------------------
# ffmpeg runners
# --------------------------------------------------------------------------
def run_ffmpeg(
    cmd: list[str],
    *,
    duration: float | None = None,
    on_progress: ProgressFn | None = None,
    on_process: ProcessFn | None = None,
    log: LogFn | None = None,
    timeout: float | None = None,
) -> None:
    """Run ffmpeg with `-progress pipe:1`, reporting 0..1 progress. Raises FFmpegError."""
    if log:
        log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if on_process:
        on_process(proc)
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for err_line in proc.stderr:
            stderr_lines.append(err_line.rstrip())
            del stderr_lines[:-50]

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        seconds = parse_progress_line(line)
        if seconds is not None and duration and on_progress:
            on_progress(min(1.0, seconds / duration))
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise FFmpegError("ffmpeg timed out")
    t.join(timeout=5)
    tail = "\n".join(stderr_lines[-20:])
    if log and tail:
        log(tail)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg exited with code {proc.returncode}", tail)
    if on_progress:
        on_progress(1.0)


def make_proxy(
    src: Path,
    dst: Path,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    has_audio: bool,
    encoder: str = "libx264",
    ffmpeg: str = "ffmpeg",
    on_progress: ProgressFn | None = None,
    on_process: ProcessFn | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Write a 720p CFR H.264 proxy to `dst` (via dst.tmp, renamed when complete).

    Falls back to libx264 if a GPU encoder fails. Returns {width, height, fps, encoder}.
    """
    w, h = proxy_size(width, height)
    rate = proxy_fps(fps)
    tmp = dst.with_name(dst.name + ".tmp")
    encoders = [encoder] if encoder == "libx264" else [encoder, "libx264"]
    last_error: FFmpegError | None = None
    for enc in encoders:
        cmd = build_proxy_cmd(src, tmp, width=w, height=h, fps=rate, encoder=enc, has_audio=has_audio, ffmpeg=ffmpeg)
        try:
            run_ffmpeg(cmd, duration=duration, on_progress=on_progress, on_process=on_process, log=log)
            tmp.replace(dst)
            return {"width": w, "height": h, "fps": rate_to_float(rate), "encoder": enc}
        except FFmpegError as exc:
            last_error = exc
            safe_unlink(tmp)
        except BaseException:
            safe_unlink(tmp)  # cancelled: the job queue already killed ffmpeg
            raise
            if log and enc != encoders[-1]:
                log(f"{enc} failed, falling back to libx264")
    assert last_error is not None
    raise last_error


def make_thumbnail(src: Path, dst: Path, *, at: float, width: int = 480, ffmpeg: str = "ffmpeg") -> bool:
    """Save one JPEG frame at `at` seconds. Returns False on failure (thumbnails are optional)."""
    tmp = dst.with_name(dst.name + ".tmp.jpg")
    cmd = [ffmpeg, "-y", "-nostdin", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(src),
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dst)
    return True


def extract_audio(
    src: Path,
    dst: Path,
    *,
    duration: float | None,
    sample_rate: int = 16000,
    ffmpeg: str = "ffmpeg",
    on_progress: ProgressFn | None = None,
    on_process: ProcessFn | None = None,
    log: LogFn | None = None,
) -> None:
    """Mono 16 kHz WAV of the first audio track (input for transcription and waveforms)."""
    tmp = dst.with_name(dst.name + ".tmp")
    cmd = [ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
           "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-f", "wav", str(tmp)]
    try:
        run_ffmpeg(cmd, duration=duration, on_progress=on_progress, on_process=on_process, log=log)
    except BaseException:
        safe_unlink(tmp)
        raise
    tmp.replace(dst)


# --------------------------------------------------------------------------
# URL download (yt-dlp)
# --------------------------------------------------------------------------
class DownloadCancelled(Exception):
    pass


def download_url(
    url: str,
    dest_dir: Path,
    *,
    ffmpeg: str = "ffmpeg",
    on_progress: ProgressFn | None = None,
    log: LogFn | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Download one video (max 1080p, merged to MP4) into dest_dir as original.<ext>.

    Returns {"path", "title"}. Raises DownloadCancelled or yt_dlp's DownloadError.
    """
    import shutil

    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    files_done: list[str] = []

    def hook(d: dict[str, Any]) -> None:
        if is_cancelled and is_cancelled():
            raise DownloadCancelled()
        if d.get("status") == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                # Video and audio are separate downloads; weight the first one as 90%.
                part = min(1.0, d.get("downloaded_bytes", 0) / total)
                base = 0.9 if files_done else 0.0
                share = 0.1 if files_done else 0.9
                on_progress(min(0.99, base + share * part))
        elif d.get("status") == "finished":
            files_done.append(d.get("filename", ""))

    class _Logger:
        def debug(self, msg: str) -> None:
            if log and not msg.startswith("[debug]"):
                log(msg)

        def info(self, msg: str) -> None:
            if log:
                log(msg)

        def warning(self, msg: str) -> None:
            if log:
                log("WARNING: " + msg)

        def error(self, msg: str) -> None:
            if log:
                log("ERROR: " + msg)

    opts: dict[str, Any] = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "original.%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "progress_hooks": [hook],
        "logger": _Logger(),
        "noprogress": True,
        "retries": 3,
    }
    ffmpeg_path = shutil.which(ffmpeg)
    if ffmpeg_path:
        opts["ffmpeg_location"] = str(Path(ffmpeg_path).parent)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("no video found at this URL")
        if "entries" in info:  # playlist URL despite noplaylist: take the first entry
            info = next(iter(info["entries"] or []), None) or {}
        path = None
        for item in info.get("requested_downloads") or []:
            path = item.get("filepath") or path
        path = path or ydl.prepare_filename(info)
    final = Path(path)
    if not final.exists():
        candidates = sorted(dest_dir.glob("original.*"), key=lambda p: p.stat().st_size, reverse=True)
        candidates = [p for p in candidates if is_video_name(p.name)]
        if not candidates:
            raise RuntimeError("download finished but no video file was written")
        final = candidates[0]
    if on_progress:
        on_progress(1.0)
    return {"path": str(final), "title": info.get("title") or final.stem}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    from app.config import get_settings
    from pipeline import preflight

    parser = argparse.ArgumentParser(description="Ingest helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("proxy")
    p.add_argument("src", type=Path)
    p.add_argument("dst", type=Path)
    p.add_argument("--encoder", default=None)
    d = sub.add_parser("download")
    d.add_argument("url")
    d.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.cmd == "proxy":
        raw = preflight.ffprobe_json(settings.ffprobe_bin, args.src)
        if raw is None:
            print("ffprobe failed", file=sys.stderr)
            return 1
        probe = preflight.parse_probe(raw, args.src.stat().st_size)
        if probe.video is None:
            print("no video stream", file=sys.stderr)
            return 1
        encoder = args.encoder or preflight.detect_encoder(preflight.tool_info(settings.ffmpeg_bin), preflight.detect_device(settings.device))
        info = make_proxy(
            args.src, args.dst, width=probe.video.display_width, height=probe.video.display_height,
            fps=probe.video.fps, duration=probe.duration, has_audio=probe.audio is not None,
            encoder=encoder, ffmpeg=settings.ffmpeg_bin,
            on_progress=lambda f: print(f"\r{f * 100:5.1f}%", end="", flush=True), log=None,
        )
        print()
        print(json.dumps(info))
        return 0
    result = download_url(args.url, args.out_dir, ffmpeg=settings.ffmpeg_bin, log=print)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
