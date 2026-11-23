"""Per-channel presets: a named bundle of look-and-feel choices (platform, caption style + position,
words per page, font size, hook on/off + position, progress bar, voice) that can be applied to a clip
in one click, or to every clip of a batch approval.

Stored in the settings store under "channels" (a list). Applying a channel only touches the sections
it owns; segments, framing, caption words, hook text and voiceover scripts are never changed.
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import errors

MAX_CHANNELS = 30
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class Channel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=60)
    platform: Literal["youtube_shorts", "tiktok", "reels"] = "youtube_shorts"
    caption_style: Literal["bold", "bounce", "clean"] = "bold"
    caption_position_y: float = Field(default=0.72, ge=0.05, le=0.95)
    words_per_page: int = Field(default=3, ge=1, le=4)
    font_size: int = Field(default=72, ge=24, le=160)
    hook_enabled: bool = True
    hook_position_y: float = Field(default=0.18, ge=0.05, le=0.95)
    progress_bar_enabled: bool = True
    progress_color: str = "#FFD400"
    voice_id: str = Field(default="", max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty name")
        return v

    @field_validator("progress_color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not HEX_COLOR.match(v):
            raise ValueError("color must be #RRGGBB")
        return v.upper()


def new_id() -> str:
    return f"ch_{secrets.token_hex(4)}"


def validate_list(value: Any) -> list[dict[str, Any]]:
    """Validate the whole list (settings PATCH sends it complete); new entries get an id."""
    if not isinstance(value, list) or len(value) > MAX_CHANNELS:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="channels")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(value):
        try:
            ch = Channel.model_validate(raw)
        except ValidationError as exc:
            fields = ",".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
            raise errors.AppError(errors.VALIDATION_ERROR, fields=f"channels.{i}.{fields}") from None
        if not ch.id or ch.id in seen or not re.fullmatch(r"ch_[0-9a-z]{4,32}", ch.id):
            ch.id = new_id()
        seen.add(ch.id)
        out.append(ch.model_dump())
    return out


def find(channel_id: str) -> dict[str, Any]:
    from app import settings_store

    for ch in settings_store.get("channels"):
        if ch.get("id") == channel_id:
            return ch
    raise errors.AppError(errors.CHANNEL_NOT_FOUND)


def edl_patch(edl: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any]:
    """The EDL sections a channel changes (complete sections, as clips_service.patch_clip expects)."""
    ch = Channel.model_validate(channel)
    captions = {**(edl.get("captions") or {}), "style": ch.caption_style, "position_y": ch.caption_position_y,
                "words_per_page": ch.words_per_page, "font_size": ch.font_size}
    hook = dict(edl.get("hook_text") or {})
    # a hook needs text: turning it on for a clip without hook text would show nothing
    hook.update(enabled=ch.hook_enabled and bool((hook.get("text") or "").strip()), position_y=ch.hook_position_y)
    patch: dict[str, Any] = {
        "platform": ch.platform,
        "captions": captions,
        "hook_text": hook,
        "progress_bar": {**(edl.get("progress_bar") or {}), "enabled": ch.progress_bar_enabled, "color": ch.progress_color},
    }
    if ch.voice_id:
        vo = dict(edl.get("voiceover") or {})
        if vo.get("voice_id") != ch.voice_id:
            # a different voice invalidates generated audio; the user regenerates (with a credit prompt)
            vo.update(voice_id=ch.voice_id, audio_path=None, duration=None, enabled=False)
        patch["voiceover"] = vo
    return patch


def apply_to_clip(clip_id: str, channel_id: str) -> dict[str, Any]:
    from app import clips_service, moments_service

    channel = find(channel_id)
    clip = clips_service._load(clip_id)
    clips_service.patch_clip(clip_id, edl_patch=edl_patch(clip.edl or {}, channel))
    if clip.moment_id is not None:
        # keep the moment's style/platform in step, so a later moment edit does not undo the channel
        try:
            moments_service.update_moment(clip.moment_id, {"caption_style": channel["caption_style"],
                                                           "platform": channel["platform"]}, snap_bounds=False)
        except errors.AppError:
            pass
    clip = clips_service._load(clip_id)
    clip.work = {**(clip.work or {}), "channel_id": channel_id}
    return clips_service.clip_out(clips_service._save(clip))
