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
MOMENTS_DUPLICATES_DROPPED = _register("MOMENTS_DUPLICATES_DROPPED", I, 400,("count", "total"))
MOMENT_NOT_FOUND = _register("MOMENT_NOT_FOUND", E, 404)
ANALYSIS_NOT_READY = _register("ANALYSIS_NOT_READY", E, 404)

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
