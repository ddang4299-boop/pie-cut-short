"""Step 9 - ElevenLabs: text-to-speech voiceover and Voice Isolator, cached by hash.

Every call that spends credits goes through `tts()` / `isolate()`, which look in the cache first:
the same text + voice + model + settings never costs twice. The caller (app/voice_service.py) shows
the estimate and asks for confirmation before anything reaches the network.

Clients:
- `ElevenLabsClient` wraps the official `elevenlabs` SDK (v2.x).
- `MockClient` (ELEVENLABS_MOCK=1, tests, autopilot) makes local tones with ffmpeg and counts calls;
  it never touches the network.

Models (docs, 2026-11): `eleven_multilingual_v2` has no Vietnamese, so the default is
`eleven_flash_v2_5` (32 languages incl. vi, about half the credits per character); `eleven_v3`
is the most expressive (70+ languages incl. vi, 5,000 characters per request).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

OUTPUT_FORMAT = "mp3_44100_128"

# credits_per_char are estimates from the pricing notes (Flash = 50 % of Multilingual/v3).
MODELS: dict[str, dict[str, Any]] = {
    "eleven_flash_v2_5": {"credits_per_char": 0.5, "max_chars": 40000, "vietnamese": True, "language_code": True},
    "eleven_v3": {"credits_per_char": 1.0, "max_chars": 5000, "vietnamese": True, "language_code": True},
    "eleven_multilingual_v2": {"credits_per_char": 1.0, "max_chars": 10000, "vietnamese": False, "language_code": False},
}
DEFAULT_MODEL = "eleven_flash_v2_5"
ISOLATION_CREDITS_PER_MINUTE = 1000  # estimate
MAX_SCRIPT_CHARS = 5000  # EDL limit (also eleven_v3's per-request limit)

DEFAULT_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "speed": 1.0}
SETTING_RANGES = {"stability": (0.0, 1.0), "similarity_boost": (0.0, 1.0), "style": (0.0, 1.0), "speed": (0.7, 1.2)}


# --------------------------------------------------------------------------
# Errors (mapped to translated error codes by the service)
# --------------------------------------------------------------------------
class VoiceError(Exception):
    """kind: not_configured | invalid_key | quota | permission | rate_limited | offline | voice_not_found | failed"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail = kind, detail[:300]


def classify_api_error(status: int | None, body: Any) -> VoiceError:
    """Map an ElevenLabs error response ({"detail": {"code", "status", "message"}}) to a VoiceError."""
    detail = body.get("detail") if isinstance(body, dict) else None
    code = ""
    message = ""
    if isinstance(detail, dict):
        code = str(detail.get("code") or detail.get("status") or "")
        message = str(detail.get("message") or "")
    elif detail is not None:
        message = str(detail)
    elif body is not None:
        message = str(body)
    if code in ("invalid_api_key", "missing_api_key") or status == 401 and code not in ("quota_exceeded",):
        return VoiceError("invalid_key", message)
    if code in ("insufficient_credits", "quota_exceeded") or status == 402:
        return VoiceError("quota", message)
    if code in ("insufficient_permissions", "missing_permissions") or status == 403:
        return VoiceError("permission", message)
    if code in ("rate_limit_exceeded", "concurrent_limit_exceeded", "too_many_concurrent_requests") or status == 429:
        return VoiceError("rate_limited", message)
    if code == "voice_not_found" or status == 404:
        return VoiceError("voice_not_found", message)
    return VoiceError("failed", f"HTTP {status}: {message or code}")


# --------------------------------------------------------------------------
# Settings, estimates, cache keys
# --------------------------------------------------------------------------
def clean_settings(settings: dict[str, Any] | None, model_id: str = DEFAULT_MODEL) -> dict[str, float]:
    out = dict(DEFAULT_SETTINGS)
    for key, (lo, hi) in SETTING_RANGES.items():
        if settings and settings.get(key) is not None:
            out[key] = round(min(hi, max(lo, float(settings[key]))), 2)
    if model_id == "eleven_v3":  # v3 takes stability as Creative 0 / Natural 0.5 / Robust 1
        out["stability"] = min((0.0, 0.5, 1.0), key=lambda v: abs(v - out["stability"]))
    return out


def model_info(model_id: str) -> dict[str, Any]:
    if model_id not in MODELS:
        raise ValueError(f"unknown model {model_id}")
    return MODELS[model_id]


def tts_estimate(text: str, model_id: str = DEFAULT_MODEL) -> dict[str, Any]:
    info = model_info(model_id)
    chars = len(text.strip())
    return {"characters": chars, "credits": int(math.ceil(chars * info["credits_per_char"])),
            "max_chars": min(info["max_chars"], MAX_SCRIPT_CHARS), "model_id": model_id}


def isolation_estimate(seconds: float) -> dict[str, Any]:
    minutes = max(0.0, seconds) / 60
    return {"seconds": round(seconds, 2), "credits": int(math.ceil(minutes * ISOLATION_CREDITS_PER_MINUTE))}


def language_code(model_id: str, language: str | None) -> str | None:
    if not language or not MODELS.get(model_id, {}).get("language_code"):
        return None
    code = language.split("-")[0].lower()
    return code if len(code) == 2 else None


def tts_key(text: str, voice_id: str, model_id: str, settings: dict[str, float], lang: str | None) -> str:
    payload = {"text": text.strip(), "voice_id": voice_id, "model_id": model_id, "settings": settings,
               "language_code": lang, "format": OUTPUT_FORMAT}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------
class VoiceClient(Protocol):
    mock: bool

    def voices(self) -> list[dict[str, Any]]: ...
    def tts(self, *, text: str, voice_id: str, model_id: str, settings: dict[str, float], language_code: str | None) -> bytes: ...
    def subscription(self) -> dict[str, Any]: ...
    def isolate(self, audio: Path) -> bytes: ...


def _voice_out(v: Any) -> dict[str, Any]:
    get = (lambda k: v.get(k)) if isinstance(v, dict) else (lambda k: getattr(v, k, None))
    labels = dict(get("labels") or {})
    langs = []
    for item in get("verified_languages") or []:
        lang = item.get("language") if isinstance(item, dict) else getattr(item, "language", None)
        if lang and lang not in langs:
            langs.append(lang)
    if labels.get("language") and labels["language"] not in langs:
        langs.insert(0, labels["language"])
    return {"voice_id": get("voice_id"), "name": get("name") or get("voice_id"), "category": get("category"),
            "labels": labels, "languages": langs, "preview_url": get("preview_url")}


class ElevenLabsClient:
    """The official SDK. Every method raises VoiceError on failure."""

    mock = False

    def __init__(self, api_key: str, timeout: float = 120):
        if not api_key:
            raise VoiceError("not_configured")
        try:
            from elevenlabs.client import ElevenLabs
        except ImportError:  # SDK missing: same as no key, the app keeps working
            raise VoiceError("not_configured", "the elevenlabs package is not installed") from None
        self.client = ElevenLabs(api_key=api_key, timeout=timeout)

    def _call(self, fn, *args, **kwargs):
        import httpx
        from elevenlabs.core.api_error import ApiError

        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            raise classify_api_error(exc.status_code, exc.body) from None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise VoiceError("offline", str(exc)) from None

    def voices(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        for _ in range(5):  # up to 500 voices
            page = self._call(self.client.voices.search, page_size=100, next_page_token=token, include_total_count=False)
            out += [_voice_out(v) for v in page.voices]
            token = page.next_page_token
            if not page.has_more or not token:
                break
        return out

    def tts(self, *, text, voice_id, model_id, settings, language_code) -> bytes:
        from elevenlabs import VoiceSettings

        def run() -> bytes:
            stream = self.client.text_to_speech.convert(
                voice_id=voice_id, text=text, model_id=model_id, output_format=OUTPUT_FORMAT,
                language_code=language_code,
                voice_settings=VoiceSettings(stability=settings["stability"], similarity_boost=settings["similarity_boost"],
                                             style=settings["style"], speed=settings["speed"], use_speaker_boost=True),
            )
            return b"".join(stream)

        return self._call(run)

    def subscription(self) -> dict[str, Any]:
        s = self._call(self.client.user.subscription.get)
        return {"tier": getattr(s, "tier", None), "character_count": getattr(s, "character_count", None),
                "character_limit": getattr(s, "character_limit", None),
                "next_reset_unix": getattr(s, "next_character_count_reset_unix", None)}

    def isolate(self, audio: Path) -> bytes:
        def run() -> bytes:
            with open(audio, "rb") as fh:
                return b"".join(self.client.audio_isolation.convert(audio=fh))

        return self._call(run)


@dataclass
class MockClient:
    """Offline stand-in: tones instead of speech, a fake subscription, a call log. No network."""

    ffmpeg: str = "ffmpeg"
    mock: bool = True
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    used: int = 0
    limit: int = 10000

    VOICES = (
        {"voice_id": "mock_vi_linh", "name": "Linh (mock)", "category": "premade",
         "labels": {"language": "vi", "gender": "female", "accent": "northern"}, "preview_url": None},
        {"voice_id": "mock_en_adam", "name": "Adam (mock)", "category": "premade",
         "labels": {"language": "en", "gender": "male", "accent": "american"}, "preview_url": None},
    )

    def voices(self) -> list[dict[str, Any]]:
        self.calls.append(("voices", {}))
        return [_voice_out(v) for v in self.VOICES]

    def tone(self, seconds: float, freq: int = 300) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tone.mp3"
            cmd = [self.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
                   "-i", f"sine=frequency={freq}:duration={seconds:.2f}:sample_rate=44100",
                   "-af", "volume=0.25,afade=t=in:d=0.05", "-c:a", "libmp3lame", "-b:a", "128k", str(out)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            return out.read_bytes()

    def tts(self, *, text, voice_id, model_id, settings, language_code) -> bytes:
        if voice_id not in {v["voice_id"] for v in self.VOICES}:
            raise VoiceError("voice_not_found", voice_id)
        credits = tts_estimate(text, model_id)["credits"]
        if self.used + credits > self.limit:
            raise VoiceError("quota", "mock quota exhausted")
        self.used += credits
        self.calls.append(("tts", {"text": text, "voice_id": voice_id, "model_id": model_id, "settings": settings}))
        seconds = max(1.0, len(text.split()) * 0.36 / max(0.7, settings.get("speed", 1.0)))
        return self.tone(min(seconds, 120.0), 260 if voice_id.startswith("mock_en") else 340)

    def subscription(self) -> dict[str, Any]:
        return {"tier": "mock", "character_count": self.used, "character_limit": self.limit, "next_reset_unix": None}

    def isolate(self, audio: Path) -> bytes:
        self.calls.append(("isolate", {"audio": str(audio)}))
        return audio.read_bytes()  # the "cleaned" audio is the input


# --------------------------------------------------------------------------
# Cached operations
# --------------------------------------------------------------------------
@dataclass
class TtsResult:
    path: Path
    key: str
    cached: bool
    credits: int
    characters: int
    duration: float | None


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float | None:
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=30)
        return round(float(json.loads(r.stdout)["format"]["duration"]), 3)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def cached_tts(cache_dir: Path, text: str, voice_id: str, model_id: str, settings: dict[str, Any] | None,
               language: str | None) -> TtsResult | None:
    s = clean_settings(settings, model_id)
    key = tts_key(text, voice_id, model_id, s, language_code(model_id, language))
    path = cache_dir / f"tts_{key}.mp3"
    if not path.exists():
        return None
    meta = _read_meta(path)
    return TtsResult(path, key, True, 0, len(text.strip()), meta.get("duration"))


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.with_suffix(".json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def tts(client: VoiceClient | None, cache_dir: Path, *, text: str, voice_id: str, model_id: str = DEFAULT_MODEL,
        settings: dict[str, Any] | None = None, language: str | None = None, ffprobe: str = "ffprobe") -> TtsResult:
    """Text -> mp3 in the cache. A cache hit makes no call and costs nothing."""
    text = text.strip()
    hit = cached_tts(cache_dir, text, voice_id, model_id, settings, language)
    if hit:
        return hit
    if client is None:
        raise VoiceError("not_configured")
    s = clean_settings(settings, model_id)
    lang = language_code(model_id, language)
    key = tts_key(text, voice_id, model_id, s, lang)
    audio = client.tts(text=text, voice_id=voice_id, model_id=model_id, settings=s, language_code=lang)
    if not audio:
        raise VoiceError("failed", "empty audio")
    path = cache_dir / f"tts_{key}.mp3"
    _write_atomic(path, audio)
    duration = probe_duration(path, ffprobe)
    est = tts_estimate(text, model_id)
    meta = {"text": text, "voice_id": voice_id, "model_id": model_id, "settings": s, "language_code": lang,
            "duration": duration, "characters": est["characters"], "credits": est["credits"], "mock": client.mock}
    _write_atomic(path.with_suffix(".json"), json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
    return TtsResult(path, key, False, est["credits"], est["characters"], duration)


def file_key(path: Path, extra: str = "") -> str:
    st = path.stat()
    return hashlib.sha256(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{extra}".encode()).hexdigest()[:32]


def isolate(client: VoiceClient | None, cache_dir: Path, audio: Path, key: str) -> tuple[Path, bool]:
    """Voice Isolator on `audio` (cached under `key`). Returns (path, cached)."""
    path = cache_dir / f"iso_{key}.mp3"
    if path.exists():
        return path, True
    if client is None:
        raise VoiceError("not_configured")
    _write_atomic(path, client.isolate(audio))
    return path, False


# --------------------------------------------------------------------------
# Script suggestion (heuristic; the service may ask an LLM provider instead)
# --------------------------------------------------------------------------
def heuristic_script(hook: str, sentences: list[str], max_words: int) -> str:
    """A short spoken intro: the hook, then the opening sentence(s) of the clip, within max_words."""
    parts: list[str] = []
    words = 0
    for text in [hook.strip(), *[s.strip() for s in sentences]]:
        if not text or text in parts:
            continue
        n = len(text.split())
        if parts and words + n > max_words:
            break
        parts.append(text if text[-1] in ".!?…" else text + ".")
        words += n
    return " ".join(parts)
