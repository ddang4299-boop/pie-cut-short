"""SQLModel tables: Source, Job, Moment, Clip, AppSetting.

JSON columns are replaced as a whole (never mutated in place) so SQLAlchemy notices changes.
Transcripts live on disk as JSON (data/<source_id>/transcript.json); the DB keeps the state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

# Pipeline steps in order. Each step stores {state, progress, error} in Source.steps.
STEPS = ("preflight", "proxy", "transcribe", "analyze", "moments")
# Step states: pending | queued | running | done | skipped | error | waiting
STEP_DONE_STATES = ("done", "skipped")

# Source.status values
SOURCE_DOWNLOADING = "downloading"  # URL import in progress
SOURCE_NEW = "new"  # preflight ran, waiting for the user to press Start
SOURCE_PROCESSING = "processing"  # pipeline queued or running
SOURCE_READY = "ready"  # every implemented step finished
SOURCE_ERROR = "error"  # a step failed (see steps)
SOURCE_WAITING = "waiting"  # waiting for the user (e.g. /find-moments in Claude Code)

CONTENT_TYPES = ("talking_head", "screen_recording", "podcast", "gameplay_facecam")
PLATFORMS = ("youtube_shorts", "tiktok", "reels")
CAPTION_STYLES = ("bold", "bounce", "clean")
MOMENT_STATUSES = ("proposed", "approved", "rejected")
MOMENT_CATEGORIES = ("opinion", "story", "tip", "fact", "funny", "reaction", "reveal", "gameplay", "manual")

# Job.status values
JOB_QUEUED, JOB_RUNNING, JOB_DONE, JOB_ERROR, JOB_CANCELLED = "queued", "running", "done", "error", "cancelled"
JOB_ACTIVE = (JOB_QUEUED, JOB_RUNNING)


def utcnow() -> datetime:
    return datetime.now(UTC)


def empty_steps() -> dict[str, dict[str, Any]]:
    return {step: {"state": "pending", "progress": 0.0, "error": None} for step in STEPS}


class Source(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "src_" + 8 hex chars
    name: str
    origin: str  # file | path | url
    url: str | None = None
    media_path: str | None = None  # absolute path of the original media
    owns_media: bool = True  # False when the original is a user file referenced in place
    file_hash: str | None = None
    size_bytes: int | None = None
    duration: float | None = None
    fps: float | None = None
    width: int | None = None  # display size (rotation applied)
    height: int | None = None
    has_audio: bool = True
    proxy_path: str | None = None
    thumbnail_path: str | None = None
    content_type: str | None = None  # user override: talking_head | screen_recording | podcast | gameplay_facecam
    content_type_detected: str | None = None  # result of pipeline/content_type.py
    content_type_info: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))  # metrics behind it
    language: str | None = None  # detected content language (vi, en, mixed, ...)
    language_override: str = "auto"  # auto | vi | en | mixed
    notes: str = ""
    target_platform: str = "youtube_shorts"  # default platform for the whole source
    moments_summary: str = ""  # "summary" from moments.json
    moments_notes: str = ""  # "notes" from moments.json
    moments_provider: str | None = None  # provider that produced the current proposals
    status: str = SOURCE_NEW
    preflight: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    steps: dict[str, Any] = Field(default_factory=empty_steps, sa_column=Column(JSON))
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # download | pipeline
    lane: str = "main"  # download | main (one worker per lane)
    source_id: str | None = Field(default=None, index=True)
    status: str = Field(default=JOB_QUEUED, index=True)
    progress: float = 0.0  # 0..1
    step: str | None = None  # current step, for the UI
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Moment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)
    start: float
    end: float
    scores: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # 5 rubric dimensions, 0..10
    score: int = 0  # weighted total, 0..100
    title: str = ""
    hook: str = ""
    reason: str = ""
    category: str = "manual"
    key_line: list[float] | None = Field(default=None, sa_column=Column(JSON))
    status: str = "proposed"  # proposed | approved | rejected
    origin: str = "ai"  # ai | manual
    provider: str | None = None
    caption_style: str = "bold"
    platform: str = "youtube_shorts"
    raw_start: float | None = None  # boundaries before snapping (as proposed / dragged)
    raw_end: float | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Clip(SQLModel, table=True):
    """One short being edited. `edl` is the Edit Decision List (app/edl.py), the single source of
    truth for preview and render. `work` tracks background steps: {"prepare": {...}, "reframe": {...}}."""

    id: str = Field(primary_key=True)  # "clip_" + 8 hex chars
    source_id: str = Field(index=True)
    moment_id: int | None = Field(default=None, index=True)
    status: str = "draft"  # draft | review | approved | rendered
    edl: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    work: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AppSetting(SQLModel, table=True):
    """Key/value app settings (JSON values). Defaults live in app/settings_store.py."""

    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))
