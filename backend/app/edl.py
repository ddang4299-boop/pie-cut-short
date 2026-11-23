"""Clip Edit Decision List: the single source of truth for preview and render (see CLAUDE.md).

`build_default()` makes the EDL of a freshly approved moment; `validate()` checks a full EDL
(after the frontend's partial update was merged) and returns it normalized (keyframes sorted,
segments sorted and inside the source). Unknown keys are dropped.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app import errors
from app.models import CAPTION_STYLES, PLATFORMS

CROP_MODES = ("auto", "manual", "split", "cursor")
CROP_FITS = ("fill", "blur")
EASINGS = ("linear", "ease_in_out")
CLIP_STATUSES = ("draft", "review", "approved", "rendered")
MIN_SEGMENT = 0.2
MAX_KEYFRAMES = 2000
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Segment(_Model):
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Keyframe(_Model):
    t: float = Field(ge=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    zoom: float = Field(default=1.0, ge=1, le=4)


def _region(v: list[float]) -> list[float]:
    if len(v) != 4:
        raise ValueError("a region is [x, y, w, h]")
    x, y, w, h = v
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.001 and y + h <= 1.001):
        raise ValueError("region outside the frame")
    return [round(float(a), 4) for a in v]


class Split(_Model):
    top: list[float]
    bottom: list[float]
    ratio: float = Field(default=0.4, ge=0.2, le=0.8)

    _check_top = field_validator("top")(lambda v: _region(v))
    _check_bottom = field_validator("bottom")(lambda v: _region(v))


class Crop(_Model):
    mode: Literal["auto", "manual", "split", "cursor"] = "auto"
    easing: Literal["linear", "ease_in_out"] = "ease_in_out"
    fit: Literal["fill", "blur"] = "fill"
    keyframes: list[Keyframe] = Field(default_factory=lambda: [Keyframe(t=0, x=0.5, y=0.5)], max_length=MAX_KEYFRAMES)
    split: Split | None = None

    @field_validator("keyframes")
    @classmethod
    def _sorted(cls, v: list[Keyframe]) -> list[Keyframe]:
        out: list[Keyframe] = []
        for k in sorted(v, key=lambda k: k.t):
            if out and abs(out[-1].t - k.t) < 1e-4:
                out[-1] = k  # same time: the later one wins
            else:
                out.append(k)
        return out


class CaptionWord(_Model):
    """A caption word in source time (see pipeline/captions.py)."""
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(default="", max_length=200)
    emphasis: bool = False

    @model_validator(mode="after")
    def _order(self) -> "CaptionWord":
        if self.end < self.start:
            self.end = self.start
        return self


class Captions(_Model):
    enabled: bool = True
    style: Literal["bold", "bounce", "clean"] = "bold"
    position_y: float = Field(default=0.72, ge=0.05, le=0.95)
    words_per_page: int = Field(default=3, ge=1, le=4)
    font_size: int = Field(default=72, ge=24, le=160)
    words: list[CaptionWord] = Field(default_factory=list, max_length=20000)


class HookText(_Model):
    enabled: bool = True
    text: str = Field(default="", max_length=200)
    start: float = Field(default=0.0, ge=0)
    end: float = Field(default=3.5, ge=0)
    position_y: float = Field(default=0.18, ge=0.05, le=0.95)


class ProgressBar(_Model):
    enabled: bool = True
    color: str = "#FFD400"

    @field_validator("color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX_COLOR.match(v):
            raise ValueError("color must be #RRGGBB")
        return v.upper()


class Voiceover(_Model):
    enabled: bool = False
    script: str = Field(default="", max_length=5000)
    voice_id: str = ""
    audio_path: str | None = None
    start: float = Field(default=0.0, ge=0)
    duck_db: float = Field(default=-12.0, ge=-40, le=0)


class Audio(_Model):
    normalize_lufs: float = Field(default=-14.0, ge=-30, le=-6)
    isolate_voice: bool = False
    volume_db: float = Field(default=0.0, ge=-30, le=12)


class Output(_Model):
    w: int = Field(default=1080, ge=240, le=4096)
    h: int = Field(default=1920, ge=240, le=4096)
    fps: int = Field(default=30, ge=12, le=60)


class EDL(_Model):
    id: str
    source_id: str
    moment_id: int | None = None
    segments: list[Segment] = Field(min_length=1, max_length=200)
    crop: Crop = Field(default_factory=Crop)
    captions: Captions = Field(default_factory=Captions)
    hook_text: HookText = Field(default_factory=HookText)
    progress_bar: ProgressBar = Field(default_factory=ProgressBar)
    voiceover: Voiceover = Field(default_factory=Voiceover)
    audio: Audio = Field(default_factory=Audio)
    platform: Literal["youtube_shorts", "tiktok", "reels"] = "youtube_shorts"
    output: Output = Field(default_factory=Output)
    status: Literal["draft", "review", "approved", "rendered"] = "draft"

    @model_validator(mode="after")
    def _segments(self) -> EDL:
        segs = sorted(self.segments, key=lambda s: s.start)
        for s in segs:
            if s.end - s.start < MIN_SEGMENT - 1e-6:
                raise ValueError("segment too short")
        for a, b in zip(segs, segs[1:]):
            if b.start < a.end - 1e-6:
                raise ValueError("segments overlap")
        self.segments = segs
        return self


def validate(data: dict[str, Any], *, source_duration: float | None = None) -> dict[str, Any]:
    """Full EDL dict -> normalized dict. Raises AppError(VALIDATION_ERROR, fields=...)."""
    try:
        edl = EDL.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"][:2]) or "edl" for e in exc.errors()})
        raise errors.AppError(errors.VALIDATION_ERROR, fields=",".join(fields)[:200]) from exc
    if source_duration and edl.segments[-1].end > source_duration + 0.05:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="segments")
    out = edl.model_dump()
    for seg in out["segments"]:
        seg["start"], seg["end"] = round(seg["start"], 3), round(seg["end"], 3)
    return out


def build_default(*, clip_id: str, source_id: str, moment: dict[str, Any], mode: str,
                  default_style: str = "bold", words: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    style = moment.get("caption_style") if moment.get("caption_style") in CAPTION_STYLES else default_style
    platform = moment.get("platform") if moment.get("platform") in PLATFORMS else "youtube_shorts"
    hook = (moment.get("hook") or "").strip()
    data = {
        "id": clip_id,
        "source_id": source_id,
        "moment_id": moment.get("id"),
        "segments": [{"start": moment["start"], "end": moment["end"]}],
        "crop": {"mode": mode if mode in CROP_MODES else "auto"},
        "captions": {"style": style, "words": words or []},
        "hook_text": {"enabled": bool(hook), "text": hook[:200]},
        "platform": platform,
    }
    return validate(data)


def merge(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Top-level merge of a partial EDL: a section in the patch replaces that whole section
    (the editor always sends complete sections). Identity fields cannot change."""
    out = dict(current)
    for key, value in patch.items():
        if key in ("id", "source_id", "moment_id"):
            continue
        out[key] = value
    return out
