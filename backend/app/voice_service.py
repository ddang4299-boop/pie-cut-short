"""Voiceover (ElevenLabs TTS) and Voice Isolator for clips.

Credit safety: `estimate_*` never calls the API. `generate` / `isolate` look in the cache first
(free); otherwise they refuse with VOICE_CONFIRM_REQUIRED (characters + estimated credits) unless
the request carries `confirm: true`, which the UI only sends after the user accepted the estimate.
Without a key (or the SDK) the voice features report VOICE_NOT_CONFIGURED and the rest of the app
keeps working.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app import clips_service, errors, settings_store
from app.config import get_settings
from pipeline import moments as mp, voice

log = logging.getLogger(__name__)
_lock = threading.Lock()
_client_override: voice.VoiceClient | None = None  # tests
_mock: voice.MockClient | None = None
_real: tuple[str, voice.ElevenLabsClient] | None = None
_voices_cache: tuple[float, list[dict[str, Any]]] | None = None
_sub_cache: tuple[float, dict[str, Any] | None, str | None] | None = None
VOICES_TTL = 600
SUB_TTL = 60

KIND_TO_CODE = {
    "not_configured": errors.VOICE_NOT_CONFIGURED,
    "invalid_key": errors.VOICE_INVALID_KEY,
    "quota": errors.VOICE_QUOTA_EXCEEDED,
    "permission": errors.VOICE_PERMISSION,
    "rate_limited": errors.VOICE_RATE_LIMITED,
    "offline": errors.VOICE_OFFLINE,
    "voice_not_found": errors.VOICE_NOT_FOUND,
}


def set_client(client: voice.VoiceClient | None) -> None:
    """Tests: use this client (None = back to the configured one); clears caches."""
    global _client_override, _voices_cache, _sub_cache
    _client_override = client
    _voices_cache = _sub_cache = None


def cache_dir() -> Path:
    return get_settings().data_path / "voice_cache"


def get_client() -> voice.VoiceClient | None:
    global _mock, _real
    if _client_override is not None:
        return _client_override
    settings = get_settings()
    with _lock:
        if settings.elevenlabs_mock:
            if _mock is None:
                _mock = voice.MockClient(ffmpeg=settings.ffmpeg_bin)
            return _mock
        key = settings.elevenlabs_api_key
        if not key:
            return None
        if _real is None or _real[0] != key:
            try:
                _real = (key, voice.ElevenLabsClient(key))
            except voice.VoiceError:
                return None
        return _real[1]


def _app_error(exc: voice.VoiceError) -> errors.AppError:
    code = KIND_TO_CODE.get(exc.kind)
    if code is None:
        return errors.AppError(errors.VOICE_FAILED, detail=exc.detail or exc.kind)
    return errors.AppError(code)


def _require_client() -> voice.VoiceClient:
    client = get_client()
    if client is None:
        raise errors.AppError(errors.VOICE_NOT_CONFIGURED)
    return client


# --------------------------------------------------------------------------
# Status / voices
# --------------------------------------------------------------------------
def subscription(refresh: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """(subscription, error code). Free call, cached for a minute."""
    global _sub_cache
    client = get_client()
    if client is None:
        return None, errors.VOICE_NOT_CONFIGURED.code
    if not refresh and _sub_cache and time.time() - _sub_cache[0] < SUB_TTL:
        return _sub_cache[1], _sub_cache[2]
    try:
        sub, err = client.subscription(), None
    except voice.VoiceError as exc:
        sub, err = None, _app_error(exc).payload["code"]
    _sub_cache = (time.time(), sub, err)
    return sub, err


def status(refresh: bool = False) -> dict[str, Any]:
    client = get_client()
    prefs = settings_store.get_all()
    sub, err = subscription(refresh) if client else (None, errors.VOICE_NOT_CONFIGURED.code)
    return {
        "configured": client is not None,
        "mock": bool(client and client.mock),
        "models": [{"id": k, **v} for k, v in voice.MODELS.items()],
        "default_model": prefs["voice_model"],
        "default_voice_id": prefs["voice_default_id"],
        "isolation_credits_per_minute": voice.ISOLATION_CREDITS_PER_MINUTE,
        "subscription": sub,
        "error": err,
    }


def voices(refresh: bool = False) -> list[dict[str, Any]]:
    global _voices_cache
    client = _require_client()
    if not refresh and _voices_cache and time.time() - _voices_cache[0] < VOICES_TTL:
        return _voices_cache[1]
    try:
        out = client.voices()
    except voice.VoiceError as exc:
        raise _app_error(exc) from exc
    if client.mock:
        for v in out:
            v["preview_url"] = f"/voice/mock-preview/{v['voice_id']}.mp3"
    _voices_cache = (time.time(), out)
    return out


def mock_preview(voice_id: str) -> Path:
    client = get_client()
    if not isinstance(client, voice.MockClient) or voice_id not in {v["voice_id"] for v in client.VOICES}:
        raise errors.AppError(errors.NOT_FOUND)
    path = cache_dir() / f"preview_{voice_id}.mp3"
    if not path.exists():
        voice._write_atomic(path, client.tone(1.5, 260 if voice_id.startswith("mock_en") else 340))
    return path


def audio_file(name: str) -> Path:
    rel = f"voice_cache/{name}"
    if not voice_path_ok(rel):
        raise errors.AppError(errors.NOT_FOUND)
    path = cache_dir() / name
    if not path.exists():
        raise errors.AppError(errors.VOICE_AUDIO_MISSING)
    return path


def voice_path_ok(rel: str) -> bool:
    from app.edl import VOICE_PATH

    return bool(VOICE_PATH.match(rel))


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------
def _clip_language(clip_id: str) -> str | None:
    clip = clips_service._load(clip_id)
    src = clips_service._source(clip.source_id)
    if src.language_override not in (None, "", "auto"):
        return src.language_override
    return src.language


def _request(clip_id: str, body: dict[str, Any]) -> dict[str, Any]:
    clip = clips_service._load(clip_id)
    vo = dict((clip.edl or {}).get("voiceover") or {})
    model_id = body.get("model_id") or vo.get("model_id") or voice.DEFAULT_MODEL
    if model_id not in voice.MODELS:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="model_id")
    script = str(body.get("script") if body.get("script") is not None else vo.get("script", "")).strip()
    voice_id = str(body.get("voice_id") or vo.get("voice_id") or settings_store.get("voice_default_id") or "")
    settings = voice.clean_settings(body.get("settings") or vo.get("settings"), model_id)
    return {"script": script, "voice_id": voice_id, "model_id": model_id, "settings": settings,
            "language": _clip_language(clip_id)}


def estimate_tts(clip_id: str, body: dict[str, Any]) -> dict[str, Any]:
    req = _request(clip_id, body)
    est = voice.tts_estimate(req["script"], req["model_id"])
    hit = req["voice_id"] and req["script"] and voice.cached_tts(
        cache_dir(), req["script"], req["voice_id"], req["model_id"], req["settings"], req["language"])
    return {**est, "cached": bool(hit), "credits_to_spend": 0 if hit else est["credits"],
            "audio_path": f"voice_cache/{hit.path.name}" if hit else None,
            "over_limit": est["characters"] > est["max_chars"], "voice_id": req["voice_id"], "settings": req["settings"]}


def _ledger(entry: dict[str, Any]) -> None:
    path = cache_dir() / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), **entry}, ensure_ascii=False) + "\n")


def generate(clip_id: str, body: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
    req = _request(clip_id, body)
    est = voice.tts_estimate(req["script"], req["model_id"])
    if not req["script"]:
        raise errors.AppError(errors.VOICE_SCRIPT_EMPTY)
    if est["characters"] > est["max_chars"]:
        raise errors.AppError(errors.VOICE_SCRIPT_TOO_LONG, max=est["max_chars"])
    if not req["voice_id"]:
        raise errors.AppError(errors.VOICE_NO_VOICE)
    hit = voice.cached_tts(cache_dir(), req["script"], req["voice_id"], req["model_id"], req["settings"], req["language"])
    if hit is None:
        client = _require_client()
        if not confirm:
            raise errors.AppError(errors.VOICE_CONFIRM_REQUIRED, characters=est["characters"], credits=est["credits"])
        try:
            result = voice.tts(client, cache_dir(), text=req["script"], voice_id=req["voice_id"], model_id=req["model_id"],
                               settings=req["settings"], language=req["language"], ffprobe=get_settings().ffprobe_bin)
        except voice.VoiceError as exc:
            raise _app_error(exc) from exc
        _ledger({"kind": "tts", "clip_id": clip_id, "characters": result.characters, "credits": result.credits,
                 "mock": client.mock, "key": result.key})
        global _sub_cache
        _sub_cache = None
    else:
        result = hit
    clip = clips_service._load(clip_id)
    vo = dict((clip.edl or {}).get("voiceover") or {})
    vo.update(enabled=True, script=req["script"], voice_id=req["voice_id"], model_id=req["model_id"],
              settings=req["settings"], audio_path=f"voice_cache/{result.path.name}", duration=result.duration)
    out = clips_service.patch_clip(clip_id, edl_patch={"voiceover": vo})
    return {"clip": out, "cached": result.cached, "credits": result.credits, "characters": result.characters}


# --------------------------------------------------------------------------
# Script suggestion
# --------------------------------------------------------------------------
SUGGEST_WORDS = 28


def _llm_script(provider: str, language: str, hook: str, excerpt: str) -> str:
    """One short request to the configured API provider; raises on any problem (caller falls back)."""
    settings = get_settings()
    lang_name = {"vi": "Vietnamese", "en": "English"}.get(language, "the same language as the transcript")
    system = ("You write spoken voiceover intros for vertical short videos. Reply with the script only: "
              f"one or two sentences, at most {SUGGEST_WORDS} words, in {lang_name}, no quotes, no emojis, no hashtags.")
    user = f"Hook idea: {hook or '(none)'}\n\nWhat the clip says:\n{excerpt}\n\nWrite the intro the narrator says before the clip."
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise mp.ProviderNotConfigured(provider)
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=1, timeout=60)
        msg = client.messages.create(model=settings.anthropic_model, max_tokens=1024, system=system,
                                     messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    elif provider == "openai":
        if not settings.openai_api_key:
            raise mp.ProviderNotConfigured(provider)
        data = mp._post_json("https://api.openai.com/v1/chat/completions",
                             {"model": settings.openai_model,
                              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                             {"authorization": f"Bearer {settings.openai_api_key}"}, provider, timeout=60)
        text = data["choices"][0]["message"]["content"] or ""
    elif provider == "ollama":
        data = mp._post_json(f"{settings.ollama_host.rstrip('/')}/api/chat",
                             {"model": settings.ollama_model, "stream": False,
                              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                             {}, provider, timeout=120)
        text = data["message"]["content"] or ""
    else:
        raise mp.ProviderNotConfigured(provider)
    text = " ".join(text.strip().strip('"“”').split())
    if not text:
        raise ValueError("empty reply")
    return text[: voice.MAX_SCRIPT_CHARS]


def suggest(clip_id: str) -> dict[str, Any]:
    from app import tasks

    clip = clips_service._load(clip_id)
    data = clip.edl or {}
    hook = ((data.get("hook_text") or {}).get("text") or "").strip()
    segs = data.get("segments") or []
    words = [w for w in (data.get("captions") or {}).get("words") or []
             if any(s["start"] <= (w["start"] + w["end"]) / 2 < s["end"] for s in segs)]
    sentences = [s["text"] for s in mp.build_sentences(words)] if words else []
    language = _clip_language(clip_id) or "en"
    provider = tasks.selected_provider()
    if provider in ("anthropic", "openai", "ollama"):
        try:
            return {"script": _llm_script(provider, language, hook, " ".join(sentences)[:4000]), "source": provider}
        except Exception as exc:  # noqa: BLE001 - any provider problem falls back to the heuristic
            log.info("voice suggest via %s failed: %r", provider, exc)
    return {"script": voice.heuristic_script(hook, sentences, SUGGEST_WORDS), "source": "heuristic"}


# --------------------------------------------------------------------------
# Voice Isolator
# --------------------------------------------------------------------------
def _isolation_input(clip_id: str) -> tuple[Path, float, float]:
    clip = clips_service._load(clip_id)
    src = clips_service._source(clip.source_id)
    media = Path(src.media_path or src.proxy_path or "")
    if not media.exists():
        raise errors.AppError(errors.FILE_NOT_FOUND, name=media.name or src.name)
    segs = (clip.edl or {}).get("segments") or []
    start = min(s["start"] for s in segs)
    end = max(s["end"] for s in segs)
    return media, start, end


def estimate_isolation(clip_id: str) -> dict[str, Any]:
    media, start, end = _isolation_input(clip_id)
    key = voice.file_key(media, f"{start:.3f}-{end:.3f}")
    cached = (cache_dir() / f"iso_{key}.mp3").exists()
    est = voice.isolation_estimate(end - start)
    return {**est, "cached": cached, "credits_to_spend": 0 if cached else est["credits"]}


def isolate(clip_id: str, *, confirm: bool) -> dict[str, Any]:
    media, start, end = _isolation_input(clip_id)
    key = voice.file_key(media, f"{start:.3f}-{end:.3f}")
    est = voice.isolation_estimate(end - start)
    target = cache_dir() / f"iso_{key}.mp3"
    cached = target.exists()
    if not cached:
        client = _require_client()
        if not confirm:
            raise errors.AppError(errors.VOICE_CONFIRM_REQUIRED, characters=0, credits=est["credits"])
        settings = get_settings()
        part = cache_dir() / f"in_{key}.mp3"
        part.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                            "-i", str(media), "-vn", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k", str(part)],
                           check=True, capture_output=True, timeout=600)
            voice.isolate(client, cache_dir(), part, key)
        except subprocess.SubprocessError as exc:
            raise errors.AppError(errors.VOICE_FAILED, detail=f"audio extraction failed: {exc}") from exc
        except voice.VoiceError as exc:
            raise _app_error(exc) from exc
        finally:
            part.unlink(missing_ok=True)
        _ledger({"kind": "isolate", "clip_id": clip_id, "seconds": est["seconds"], "credits": est["credits"], "mock": client.mock})
    clip = clips_service._load(clip_id)
    audio = dict((clip.edl or {}).get("audio") or {})
    audio.update(isolate_voice=True, isolated_path=f"voice_cache/{target.name}", isolated_start=round(start, 3))
    out = clips_service.patch_clip(clip_id, edl_patch={"audio": audio})
    return {"clip": out, "cached": cached, "credits": 0 if cached else est["credits"]}
