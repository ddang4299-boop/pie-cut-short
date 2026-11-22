"""Step 0 — Preflight: check the machine and a source file before any heavy work runs.

Pure logic (`parse_probe`, `evaluate`, `machine_issues`) is kept apart from the
subprocess calls so it can be unit-tested without ffmpeg.

Output is a JSON-serializable dict with a list of issues (error codes + params, see
app/errors.py); the frontend translates them.

CLI (run from backend/):
    python -m pipeline.preflight path/to/video.mp4 [--data-dir DIR]
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from app import errors

DISK_FACTOR = 3  # free disk must be >= 3x the source size
GIB = 1024**3
LOW_RES_SHORT_SIDE = 720  # warn below 720p: 9:16 crops would be upscaled a lot
VERTICAL_RATIO = 1.7  # height / width at or above this counts as already vertical (9:16 = 1.78)
VERY_LONG_SECONDS = 2 * 3600
VFR_TOLERANCE = 0.01  # relative difference between r_frame_rate and avg_frame_rate

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@dataclass
class VideoStream:
    codec: str
    width: int
    height: int
    display_width: int  # after applying rotation metadata
    display_height: int
    fps: float  # r_frame_rate
    avg_fps: float
    rotation: int
    pix_fmt: str | None


@dataclass
class AudioStream:
    codec: str
    sample_rate: int | None
    channels: int | None


@dataclass
class ProbeInfo:
    duration: float
    size_bytes: int
    format_name: str
    video: VideoStream | None
    audio: AudioStream | None
    is_vfr: bool


@dataclass
class ToolInfo:
    available: bool
    version: str | None = None
    path: str | None = None


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------
def _rate(value: str | None) -> float:
    """Parse an ffprobe rate like '30000/1001' (or '0/0') into a float."""
    if not value:
        return 0.0
    try:
        frac = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return float(frac)


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"])))
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate", 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_probe(raw: dict[str, Any], size_bytes: int) -> ProbeInfo:
    """Turn `ffprobe -show_format -show_streams -print_format json` output into ProbeInfo."""
    fmt = raw.get("format") or {}
    streams = raw.get("streams") or []

    # Cover art in audio files shows up as a video stream with attached_pic=1: ignore it.
    video_raw = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio_raw = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _float(fmt.get("duration"))
    if duration <= 0:
        duration = max((_float(s.get("duration")) for s in streams), default=0.0)

    video = None
    is_vfr = False
    if video_raw:
        width = int(video_raw.get("width") or 0)
        height = int(video_raw.get("height") or 0)
        rotation = _rotation(video_raw)
        swap = abs(rotation) % 180 == 90
        fps = _rate(video_raw.get("r_frame_rate"))
        avg_fps = _rate(video_raw.get("avg_frame_rate"))
        video = VideoStream(
            codec=video_raw.get("codec_name") or "unknown",
            width=width,
            height=height,
            display_width=height if swap else width,
            display_height=width if swap else height,
            fps=round(fps, 3),
            avg_fps=round(avg_fps, 3),
            rotation=rotation,
            pix_fmt=video_raw.get("pix_fmt"),
        )
        if fps > 0 and avg_fps > 0:
            is_vfr = abs(fps - avg_fps) / fps > VFR_TOLERANCE

    audio = None
    if audio_raw:
        audio = AudioStream(
            codec=audio_raw.get("codec_name") or "unknown",
            sample_rate=_int_or_none(audio_raw.get("sample_rate")),
            channels=_int_or_none(audio_raw.get("channels")),
        )

    return ProbeInfo(
        duration=round(duration, 3),
        size_bytes=size_bytes,
        format_name=fmt.get("format_name") or "unknown",
        video=video,
        audio=audio,
        is_vfr=is_vfr,
    )


def sort_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i["severity"], 9))


def evaluate(
    probe: ProbeInfo,
    *,
    free_bytes: int,
    device: str,
    video_decodable: bool = True,
    audio_decodable: bool = True,
) -> list[dict[str, Any]]:
    """All file + machine checks that depend on probe results. Pure function."""
    out: list[dict[str, Any]] = []

    if probe.video is None:
        out.append(errors.issue(errors.NO_VIDEO_STREAM))
    elif not video_decodable:
        out.append(errors.issue(errors.VIDEO_DECODE_FAILED, codec=probe.video.codec))

    if probe.duration <= 0:
        out.append(errors.issue(errors.INVALID_DURATION))

    if probe.audio is None:
        out.append(errors.issue(errors.NO_AUDIO_STREAM))
    elif not audio_decodable:
        out.append(errors.issue(errors.AUDIO_DECODE_FAILED, codec=probe.audio.codec))

    needed = DISK_FACTOR * probe.size_bytes
    if free_bytes < needed:
        out.append(
            errors.issue(
                errors.DISK_SPACE_LOW,
                needed_gb=round(needed / GIB, 1),
                free_gb=round(free_bytes / GIB, 1),
            )
        )

    if device == "cpu":
        out.append(errors.issue(errors.GPU_NOT_AVAILABLE))

    v = probe.video
    if v is not None and v.display_width > 0 and v.display_height > 0:
        if min(v.display_width, v.display_height) < LOW_RES_SHORT_SIDE:
            out.append(
                errors.issue(
                    errors.LOW_RESOLUTION, width=v.display_width, height=v.display_height
                )
            )
        if v.display_height / v.display_width >= VERTICAL_RATIO:
            out.append(errors.issue(errors.SOURCE_ALREADY_VERTICAL))

    if probe.is_vfr:
        out.append(errors.issue(errors.VARIABLE_FRAME_RATE))

    if probe.duration > VERY_LONG_SECONDS:
        out.append(errors.issue(errors.VERY_LONG_VIDEO, hours=round(probe.duration / 3600, 1)))

    return sort_issues(out)


def tool_issues(ffmpeg: ToolInfo, ffprobe: ToolInfo) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not ffmpeg.available:
        out.append(errors.issue(errors.FFMPEG_NOT_FOUND))
    if not ffprobe.available:
        out.append(errors.issue(errors.FFPROBE_NOT_FOUND))
    return out


def machine_issues(ffmpeg: ToolInfo, ffprobe: ToolInfo, device: str) -> list[dict[str, Any]]:
    """Issues about the machine itself (used by /health). Pure function."""
    out = tool_issues(ffmpeg, ffprobe)
    if device == "cpu":
        out.append(errors.issue(errors.GPU_NOT_AVAILABLE))
    return out


def has_errors(issues: list[dict[str, Any]]) -> bool:
    return any(i["severity"] == "error" for i in issues)


# --------------------------------------------------------------------------
# Machine detection (subprocess)
# --------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def tool_info(binary: str) -> ToolInfo:
    """Locate ffmpeg/ffprobe and read its version, e.g. '9.0.2-essentials_build-www.gyan.dev'."""
    path = shutil.which(binary)
    if not path:
        return ToolInfo(available=False)
    result = _run([path, "-version"], timeout=10)
    if result is None or result.returncode != 0:
        return ToolInfo(available=False, path=path)
    first = (result.stdout.splitlines() or [""])[0]
    match = re.match(r"\S+ version (\S+)", first)
    return ToolInfo(available=True, version=match.group(1) if match else first.strip(), path=path)


def gpu_info() -> dict[str, Any] | None:
    """Name + memory of the first NVIDIA GPU via nvidia-smi, or Apple Silicon, else None."""
    if shutil.which("nvidia-smi"):
        result = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout=10,
        )
        if result is not None and result.returncode == 0 and result.stdout.strip():
            name, _, memory = result.stdout.strip().splitlines()[0].partition(",")
            return {"vendor": "nvidia", "name": name.strip(), "memory_mb": _int_or_none(memory.strip())}
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return {"vendor": "apple", "name": "Apple Silicon", "memory_mb": None}
    return None


def detect_device(preference: str = "auto", gpu: dict[str, Any] | None = None) -> str:
    """cuda | mps | cpu. A forced preference is honored only if that hardware exists."""
    gpu = gpu if gpu is not None else gpu_info()
    vendor = (gpu or {}).get("vendor")
    available = {"cuda": vendor == "nvidia", "mps": vendor == "apple", "cpu": True}
    pref = (preference or "auto").lower()
    if pref in available:
        return pref if available[pref] else "cpu"
    if available["cuda"]:
        return "cuda"
    if available["mps"]:
        return "mps"
    return "cpu"


def detect_encoder(ffmpeg: ToolInfo, device: str) -> str:
    """Preferred H.264 encoder candidate. Render (Phase 6) still falls back if it fails."""
    if not ffmpeg.available or not ffmpeg.path:
        return "libx264"
    result = _run([ffmpeg.path, "-hide_banner", "-encoders"], timeout=10)
    listing = result.stdout if result is not None else ""
    if device == "cuda" and "h264_nvenc" in listing:
        return "h264_nvenc"
    if platform.system() == "Darwin" and "h264_videotoolbox" in listing:
        return "h264_videotoolbox"
    return "libx264"


def system_info(ffmpeg_bin: str, ffprobe_bin: str, device_pref: str) -> dict[str, Any]:
    ffmpeg = tool_info(ffmpeg_bin)
    ffprobe = tool_info(ffprobe_bin)
    gpu = gpu_info()
    device = detect_device(device_pref, gpu)
    return {
        "ffmpeg": asdict(ffmpeg),
        "ffprobe": asdict(ffprobe),
        "device": device,
        "gpu": gpu,
        "encoder": detect_encoder(ffmpeg, device),
        "issues": machine_issues(ffmpeg, ffprobe, device),
    }


# --------------------------------------------------------------------------
# File checks (subprocess)
# --------------------------------------------------------------------------
def ffprobe_json(ffprobe_path: str, path: Path) -> dict[str, Any] | None:
    result = _run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("format") else None


def can_decode(
    ffmpeg_path: str, path: Path, stream: str, start: float = 0.0, seconds: float = 2.0, strict: bool = True
) -> bool:
    """Decode a short stretch of the first video ('v') or audio ('a') stream to null."""
    cmd = [ffmpeg_path, "-v", "error", "-nostdin"]
    if strict:
        cmd.append("-xerror")
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path), "-map", f"0:{stream}:0", "-t", f"{seconds:.3f}", "-f", "null", "-"]
    result = _run(cmd, timeout=120)
    return result is not None and result.returncode == 0


def _existing_parent(path: Path) -> Path:
    path = path.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def run_preflight(
    path: Path,
    data_dir: Path,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    device_pref: str = "auto",
) -> dict[str, Any]:
    path = Path(path)
    result: dict[str, Any] = {
        "ok": False,
        "file": {"name": path.name, "path": str(path), "size_bytes": None},
        "probe": None,
        "device": None,
        "issues": [],
    }

    ffmpeg, ffprobe = tool_info(ffmpeg_bin), tool_info(ffprobe_bin)
    missing_tools = tool_issues(ffmpeg, ffprobe)
    if missing_tools:
        result["issues"] = missing_tools
        return result

    if not path.is_file():
        result["issues"] = [errors.issue(errors.FILE_NOT_FOUND, name=path.name)]
        return result
    size = path.stat().st_size
    result["file"]["size_bytes"] = size
    if size == 0:
        result["issues"] = [errors.issue(errors.FILE_EMPTY, name=path.name)]
        return result

    raw = ffprobe_json(ffprobe.path or ffprobe_bin, path)
    if raw is None:
        result["issues"] = [errors.issue(errors.PROBE_FAILED, name=path.name)]
        return result
    probe = parse_probe(raw, size)
    result["probe"] = asdict(probe)

    ffmpeg_path = ffmpeg.path or ffmpeg_bin
    video_ok = True
    if probe.video is not None:
        video_ok = can_decode(ffmpeg_path, path, "v", start=0.0, seconds=2.0)
        if video_ok and probe.duration > 4:
            video_ok = can_decode(ffmpeg_path, path, "v", start=probe.duration / 2, seconds=1.0, strict=False)
    audio_ok = probe.audio is None or can_decode(ffmpeg_path, path, "a", start=0.0, seconds=2.0)

    device = detect_device(device_pref)
    result["device"] = device
    free = shutil.disk_usage(_existing_parent(Path(data_dir))).free
    result["issues"] = evaluate(
        probe,
        free_bytes=free,
        device=device,
        video_decodable=video_ok,
        audio_decodable=audio_ok,
    )
    result["ok"] = not has_errors(result["issues"])
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Preflight-check a video file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    result = run_preflight(
        args.path,
        args.data_dir or settings.data_path,
        settings.ffmpeg_bin,
        settings.ffprobe_bin,
        settings.device,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
