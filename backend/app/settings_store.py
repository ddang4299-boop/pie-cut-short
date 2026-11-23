"""User-editable app settings stored in SQLite (AppSetting key/value rows).

Environment variables (.env, app/config.py) hold secrets and machine paths; this store holds
preferences the UI edits. Missing keys fall back to DEFAULTS.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlmodel import select

from app import db, errors
from app.models import CAPTION_STYLES, PLATFORMS, AppSetting

from pipeline.voice import MODELS as VOICE_MODELS
from pipeline.moments import DEFAULT_WEIGHTS, PROVIDERS as LLM_PROVIDERS, RUBRIC

UI_LANGUAGES = ("en", "vi")

DEFAULTS: dict[str, Any] = {
    "ui_language": "en",
    "rubric_weights": dict(DEFAULT_WEIGHTS),
    "llm_provider": None,  # None = use LLM_PROVIDER from .env
    "default_platform": "youtube_shorts",
    "default_caption_style": "bold",
    "clip_min": 15,
    "clip_max": 60,
    "moments_wanted": [8, 12],
    "voice_default_id": "",
    "voice_model": "eleven_flash_v2_5",
    "renderer": "remotion",  # remotion | ffmpeg
    "platform_presets": {},  # per-platform overrides of pipeline.presets.DEFAULT_PRESETS
    "output_dir": "",  # "" = <data>/output
    "whisper_model": None,  # None = WHISPER_MODEL from .env
    "device": None,  # None = DEVICE from .env (auto | cuda | mps | cpu)
    "channels": [],  # per-channel presets, see app/channels.py
}
RENDERERS = ("remotion", "ffmpeg")
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
DEVICES = ("auto", "cuda", "mps", "cpu")


def _validate(key: str, value: Any) -> Any:
    bad = errors.AppError(errors.VALIDATION_ERROR, fields=key)
    if key not in DEFAULTS:
        raise bad
    if key == "ui_language" and value not in UI_LANGUAGES:
        raise bad
    if key == "llm_provider" and value is not None and value not in LLM_PROVIDERS:
        raise bad
    if key == "default_platform" and value not in PLATFORMS:
        raise bad
    if key == "default_caption_style" and value not in CAPTION_STYLES:
        raise bad
    if key == "voice_default_id" and not (isinstance(value, str) and len(value) <= 100):
        raise bad
    if key == "voice_model" and value not in VOICE_MODELS:
        raise bad
    if key == "renderer" and value not in RENDERERS:
        raise bad
    if key == "whisper_model" and value is not None and value not in WHISPER_MODELS:
        raise bad
    if key == "device" and value is not None and value not in DEVICES:
        raise bad
    if key == "channels":
        from app import channels

        value = channels.validate_list(value)
    if key == "output_dir" and not (isinstance(value, str) and len(value) <= 500):
        raise bad
    if key == "platform_presets":
        from pipeline.presets import PLATFORMS as PRESET_PLATFORMS, resolve

        if not isinstance(value, dict) or not set(value) <= set(PRESET_PLATFORMS):
            raise bad
        if not all(isinstance(v, dict) for v in value.values()):
            raise bad
        # keep only the keys the preset understands, clamped
        value = {p: {k: resolve(p, value)[k] for k in v if k in ("max_seconds", "video_bitrate_mbps",
                                                                  "audio_bitrate_kbps", "fps")}
                 for p, v in value.items()}
    if key == "rubric_weights":
        if not isinstance(value, dict) or set(value) != set(RUBRIC):
            raise bad
        try:
            value = {k: float(value[k]) for k in RUBRIC}
        except (TypeError, ValueError):
            raise bad from None
        if any(v < 0 for v in value.values()) or sum(value.values()) <= 0:
            raise bad
    if key in ("clip_min", "clip_max"):
        if not isinstance(value, (int, float)) or not 3 <= value <= 600:
            raise bad
    if key == "moments_wanted":
        if not (isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value)
                and 1 <= value[0] <= value[1] <= 50):
            raise bad
    return value


def get_all() -> dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    with db.session() as s:
        for row in s.exec(select(AppSetting)).all():
            if row.key in out:
                out[row.key] = row.value
    return out


def get(key: str) -> Any:
    return get_all()[key]


def update(changes: dict[str, Any]) -> dict[str, Any]:
    clean = {k: _validate(k, v) for k, v in changes.items()}
    merged = {**get_all(), **clean}
    if merged["clip_min"] >= merged["clip_max"]:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="clip_min,clip_max")
    with db.session() as s:
        for key, value in clean.items():
            row = s.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key, value=value)
            else:
                row.value = value
            s.add(row)
        s.commit()
    return get_all()


def rubric_weights() -> dict[str, float]:
    return dict(get("rubric_weights"))
