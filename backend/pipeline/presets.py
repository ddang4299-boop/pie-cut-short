"""Platform presets for export: length limit, encoding targets and UI safe zones (pure data).

Defaults follow the CLAUDE.md table; Settings can override any field per platform (see
`resolve`). Safe zones are fractions of the output frame that the platform's own UI covers.
"""

from __future__ import annotations

import copy
from typing import Any

PLATFORMS = ("youtube_shorts", "tiktok", "reels")

DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "youtube_shorts": {
        "max_seconds": 60,
        "video_bitrate_mbps": 12,
        "audio_bitrate_kbps": 192,
        "fps": None,  # keep the clip's own fps
        # the right-side action buttons + the bottom title/channel row
        "safe_zone": {"top": 0.08, "bottom": 0.18, "left": 0.04, "right": 0.14},
    },
    "tiktok": {
        "max_seconds": 180,
        "video_bitrate_mbps": 10,
        "audio_bitrate_kbps": 192,
        "fps": None,
        "safe_zone": {"top": 0.08, "bottom": 0.20, "left": 0.04, "right": 0.14},
    },
    "reels": {
        "max_seconds": 90,
        "video_bitrate_mbps": 8,
        "audio_bitrate_kbps": 192,
        "fps": 30,
        "safe_zone": {"top": 0.10, "bottom": 0.20, "left": 0.04, "right": 0.12},
    },
}

_NUMERIC = {"max_seconds": (5, 600), "video_bitrate_mbps": (1, 50), "audio_bitrate_kbps": (64, 320)}


def resolve(platform: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """The preset of a platform with the user's overrides (from Settings) applied and clamped."""
    base = copy.deepcopy(DEFAULT_PRESETS.get(platform) or DEFAULT_PRESETS["youtube_shorts"])
    for key, value in ((overrides or {}).get(platform) or {}).items():
        if key in _NUMERIC and isinstance(value, (int, float)):
            lo, hi = _NUMERIC[key]
            base[key] = min(hi, max(lo, value))
        elif key == "fps" and (value is None or (isinstance(value, int) and 12 <= value <= 60)):
            base[key] = value
    base["platform"] = platform if platform in DEFAULT_PRESETS else "youtube_shorts"
    return base


def length_warning(duration: float, preset: dict[str, Any]) -> dict[str, Any] | None:
    """Params of a CLIP_TOO_LONG warning when the clip exceeds the platform's limit."""
    limit = float(preset["max_seconds"])
    if duration > limit + 0.05:
        return {"seconds": round(duration, 1), "max": int(limit), "platform": preset["platform"]}
    return None


def all_presets(overrides: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    return {p: resolve(p, overrides) for p in PLATFORMS}
