"""Error code registry.

The backend never sends user-facing text. It sends a code plus params, e.g.
    {"code": "DISK_SPACE_LOW", "severity": "error", "params": {"needed_gb": 12, "free_gb": 3.5}}
and the frontend translates `errors.<CODE>` from its locale files (en.json / vi.json).
A test checks that every code registered here has a translation in both locales.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"  # blocks the pipeline
    WARNING = "warning"  # works, but degraded (e.g. CPU fallback)
    INFO = "info"  # handled automatically, shown for transparency


@dataclass(frozen=True)
class ErrorCode:
    code: str
    severity: Severity
    http_status: int = 400
    params: tuple[str, ...] = ()  # names of the params the translation expects


REGISTRY: dict[str, ErrorCode] = {}


def _register(
    code: str, severity: Severity, http_status: int = 400, params: tuple[str, ...] = ()
) -> ErrorCode:
    if code in REGISTRY:
        raise ValueError(f"duplicate error code {code}")
    entry = ErrorCode(code, severity, http_status, params)
    REGISTRY[code] = entry
    return entry


E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO

# --- Generic API errors ---------------------------------------------------
INTERNAL_ERROR = _register("INTERNAL_ERROR", E, 500)
NOT_FOUND = _register("NOT_FOUND", E, 404)
VALIDATION_ERROR = _register("VALIDATION_ERROR", E, 422, ("fields",))

# --- Tools / machine ------------------------------------------------------
FFMPEG_NOT_FOUND = _register("FFMPEG_NOT_FOUND", E, 500)
FFPROBE_NOT_FOUND = _register("FFPROBE_NOT_FOUND", E, 500)
GPU_NOT_AVAILABLE = _register("GPU_NOT_AVAILABLE", W)
DISK_SPACE_LOW = _register("DISK_SPACE_LOW", E, 507, ("needed_gb", "free_gb"))

# --- Source file ----------------------------------------------------------
FILE_NOT_FOUND = _register("FILE_NOT_FOUND", E, 404, ("name",))
FILE_EMPTY = _register("FILE_EMPTY", E, 400, ("name",))
PROBE_FAILED = _register("PROBE_FAILED", E, 400, ("name",))
INVALID_DURATION = _register("INVALID_DURATION", E)
NO_VIDEO_STREAM = _register("NO_VIDEO_STREAM", E)
NO_AUDIO_STREAM = _register("NO_AUDIO_STREAM", W)
VIDEO_DECODE_FAILED = _register("VIDEO_DECODE_FAILED", E, 400, ("codec",))
AUDIO_DECODE_FAILED = _register("AUDIO_DECODE_FAILED", E, 400, ("codec",))
LOW_RESOLUTION = _register("LOW_RESOLUTION", W, 400, ("width", "height"))
VARIABLE_FRAME_RATE = _register("VARIABLE_FRAME_RATE", I)
SOURCE_ALREADY_VERTICAL = _register("SOURCE_ALREADY_VERTICAL", I)
VERY_LONG_VIDEO = _register("VERY_LONG_VIDEO", I, 400, ("hours",))

# --- Import (Phase 1) -----------------------------------------------------
UNSUPPORTED_FILE_TYPE = _register("UNSUPPORTED_FILE_TYPE", E, 415, ("name",))
INVALID_LOCATION = _register("INVALID_LOCATION", E, 400)
UPLOAD_FAILED = _register("UPLOAD_FAILED", E, 400)
DOWNLOAD_FAILED = _register("DOWNLOAD_FAILED", E, 502, ("detail",))
DISK_FULL = _register("DISK_FULL", E, 507)

# --- Sources / jobs -------------------------------------------------------
SOURCE_NOT_FOUND = _register("SOURCE_NOT_FOUND", E, 404)
SOURCE_BUSY = _register("SOURCE_BUSY", E, 409)
SOURCE_NOT_STARTABLE = _register("SOURCE_NOT_STARTABLE", E, 409)
INVALID_STEP = _register("INVALID_STEP", E, 400, ("step",))
JOB_NOT_FOUND = _register("JOB_NOT_FOUND", E, 404)
JOB_CANCELLED = _register("JOB_CANCELLED", I)
JOB_FAILED = _register("JOB_FAILED", E, 500)
STEP_NOT_AVAILABLE = _register("STEP_NOT_AVAILABLE", I)  # step arrives in a later build

# --- Proxy / transcription ------------------------------------------------
PROXY_FAILED = _register("PROXY_FAILED", E, 500)
TRANSCRIBE_FAILED = _register("TRANSCRIBE_FAILED", E, 500, ("detail",))
TRANSCRIBE_SKIPPED_NO_AUDIO = _register("TRANSCRIBE_SKIPPED_NO_AUDIO", I)
TRANSCRIPT_NOT_READY = _register("TRANSCRIPT_NOT_READY", E, 404)
NO_SPEECH_FOUND = _register("NO_SPEECH_FOUND", W)
TRANSCRIBE_CPU_FALLBACK = _register("TRANSCRIBE_CPU_FALLBACK", W)

# --- Analysis / moments (Phase 2) -----------------------------------------
ANALYZE_FAILED = _register("ANALYZE_FAILED", E, 500, ("detail",))
CONTENT_TYPE_FAILED = _register("CONTENT_TYPE_FAILED", W)  # detection failed, defaults used
MOMENTS_SKIPPED_NO_TRANSCRIPT = _register("MOMENTS_SKIPPED_NO_TRANSCRIPT", I)
MOMENTS_INVALID = _register("MOMENTS_INVALID", E, 422, ("detail",))
MOMENTS_PROVIDER_FAILED = _register("MOMENTS_PROVIDER_FAILED", E, 502, ("provider", "detail"))
MOMENTS_PROVIDER_NOT_CONFIGURED = _register("MOMENTS_PROVIDER_NOT_CONFIGURED", E, 400, ("provider",))
MOMENTS_FEWER_THAN_WANTED = _register("MOMENTS_FEWER_THAN_WANTED", W, 400, ("count",))
MOMENTS_DUPLICATES_DROPPED = _register("MOMENTS_DUPLICATES_DROPPED", I, 400, ("count", "total"))
MOMENT_NOT_FOUND = _register("MOMENT_NOT_FOUND", E, 404)
ANALYSIS_NOT_READY = _register("ANALYSIS_NOT_READY", E, 404)

# --- Clips / editor (Phase 3) ---------------------------------------------
CLIP_NOT_FOUND = _register("CLIP_NOT_FOUND", E, 404)
CLIP_BUSY = _register("CLIP_BUSY", E, 409)
PREPARE_FAILED = _register("PREPARE_FAILED", E, 500, ("detail",))
REFRAME_FAILED = _register("REFRAME_FAILED", E, 500, ("detail",))

# --- Voice / ElevenLabs (Phase 5) ------------------------------------------
VOICE_NOT_CONFIGURED = _register("VOICE_NOT_CONFIGURED", W, 400)
VOICE_INVALID_KEY = _register("VOICE_INVALID_KEY", E, 400)
VOICE_QUOTA_EXCEEDED = _register("VOICE_QUOTA_EXCEEDED", E, 402)
VOICE_PERMISSION = _register("VOICE_PERMISSION", E, 403)
VOICE_RATE_LIMITED = _register("VOICE_RATE_LIMITED", W, 429)
VOICE_OFFLINE = _register("VOICE_OFFLINE", E, 503)
VOICE_NOT_FOUND = _register("VOICE_NOT_FOUND", E, 404)
VOICE_FAILED = _register("VOICE_FAILED", E, 502, ("detail",))
VOICE_CONFIRM_REQUIRED = _register("VOICE_CONFIRM_REQUIRED", I, 409, ("characters", "credits"))
VOICE_SCRIPT_EMPTY = _register("VOICE_SCRIPT_EMPTY", E, 400)
VOICE_SCRIPT_TOO_LONG = _register("VOICE_SCRIPT_TOO_LONG", E, 400, ("max",))
VOICE_NO_VOICE = _register("VOICE_NO_VOICE", E, 400)
VOICE_AUDIO_MISSING = _register("VOICE_AUDIO_MISSING", E, 404)

# --- Render / export ------------------------------------------------------
RENDER_FAILED = _register("RENDER_FAILED", E, 500, ("detail",))
RENDER_REMOTION_FAILED = _register("RENDER_REMOTION_FAILED", W, 500, ("detail",))
RENDER_ENCODER_FALLBACK = _register("RENDER_ENCODER_FALLBACK", W, 500, ("encoder",))
RENDER_SSIM_LOW = _register("RENDER_SSIM_LOW", W, 500, ("ssim",))
RENDER_LOUDNESS_OFF = _register("RENDER_LOUDNESS_OFF", W, 500, ("lufs", "target"))
RENDER_NOT_FOUND = _register("RENDER_NOT_FOUND", E, 404)
RENDER_BUSY = _register("RENDER_BUSY", W, 409)
CLIP_TOO_LONG = _register("CLIP_TOO_LONG", W, 400, ("seconds", "max", "platform"))
EXPORT_FAILED = _register("EXPORT_FAILED", E, 500, ("detail",))
IMPORT_INVALID_FILE = _register("IMPORT_INVALID_FILE", E, 400, ("name",))
OPEN_FOLDER_FAILED = _register("OPEN_FOLDER_FAILED", W, 500)

del E, W, I


def issue(code: ErrorCode, **params: Any) -> dict[str, Any]:
    """Build a serializable issue dict, checking that params match the registry."""
    expected = set(code.params)
    if set(params) != expected:
        raise ValueError(f"{code.code} expects params {sorted(expected)}, got {sorted(params)}")
    return {"code": code.code, "severity": code.severity.value, "params": params}


class AppError(Exception):
    """Raise from routes/pipeline; FastAPI turns it into {"code", "severity", "params"}."""

    def __init__(self, code: ErrorCode, http_status: int | None = None, **params: Any):
        self.payload = issue(code, **params)
        self.http_status = http_status or code.http_status
        super().__init__(code.code)

    def to_dict(self) -> dict[str, Any]:
        return self.payload
