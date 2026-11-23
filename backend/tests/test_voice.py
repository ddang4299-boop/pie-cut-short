"""ElevenLabs voiceover / isolator: estimates, error mapping, hash cache, confirmation and the API.

Never calls the real API: a MockClient (local ffmpeg tones) or small fakes stand in.
"""

from pathlib import Path

import pytest

from app import voice_service
from pipeline import voice
from tests.test_api_clips import ready_source  # noqa: F401 - fixture
from tests.test_api_moments import doc, waiting_source, write_result  # noqa: F401 - fixtures
from tests.test_api_sources import client, fake_transcriber, run_queue  # noqa: F401 - fixtures


class CountingClient:
    mock = True

    def __init__(self):
        self.calls = 0

    def tts(self, **kwargs):
        self.calls += 1
        return b"ID3fake-mp3-" + kwargs["text"].encode()


def test_estimates_and_settings():
    assert voice.tts_estimate("  xin chào  ", "eleven_flash_v2_5") == {
        "characters": 8, "credits": 4, "max_chars": 5000, "model_id": "eleven_flash_v2_5"}
    assert voice.tts_estimate("abc", "eleven_v3")["credits"] == 3
    assert voice.isolation_estimate(90)["credits"] == 1500
    s = voice.clean_settings({"stability": 0.62, "speed": 3}, "eleven_v3")
    assert s["stability"] == 0.5 and s["speed"] == 1.2 and s["similarity_boost"] == 0.75
    assert voice.clean_settings({"stability": 0.62})["stability"] == 0.62
    assert voice.language_code("eleven_flash_v2_5", "vi") == "vi"
    assert voice.language_code("eleven_multilingual_v2", "vi") is None  # not supported by that model
    assert voice.language_code("eleven_v3", "mixed") is None


@pytest.mark.parametrize("status,body,kind", [
    (401, {"detail": {"code": "invalid_api_key", "message": "bad"}}, "invalid_key"),
    (402, {"detail": {"code": "insufficient_credits"}}, "quota"),
    (401, {"detail": {"status": "quota_exceeded", "message": "quota"}}, "quota"),  # legacy shape
    (403, {"detail": {"code": "insufficient_permissions"}}, "permission"),
    (429, {"detail": {"code": "rate_limit_exceeded"}}, "rate_limited"),
    (404, {"detail": {"code": "voice_not_found"}}, "voice_not_found"),
    (500, "oops", "failed"),
])
def test_classify_api_error(status, body, kind):
    assert voice.classify_api_error(status, body).kind == kind


def test_tts_cache_makes_repeats_free(tmp_path: Path):
    fake = CountingClient()
    args = {"text": "Xin chào các bạn", "voice_id": "v1", "model_id": "eleven_flash_v2_5", "language": "vi"}
    first = voice.tts(fake, tmp_path, **args)
    assert not first.cached and first.credits == 8 and fake.calls == 1 and first.path.exists()
    again = voice.tts(fake, tmp_path, **{**args, "text": "  Xin chào các bạn "})  # same text after strip
    assert again.cached and again.credits == 0 and again.path == first.path and fake.calls == 1
    voice.tts(fake, tmp_path, **args, settings={"speed": 1.1})  # different settings -> new audio
    assert fake.calls == 2
    assert voice.tts(None, tmp_path, **args).cached  # cached audio works without a client
    with pytest.raises(voice.VoiceError):
        voice.tts(None, tmp_path, **{**args, "text": "new"})


def test_heuristic_script():
    text = voice.heuristic_script("Đừng bỏ lỡ", ["Hôm nay tôi kể chuyện", "Một câu rất dài " * 10], 12)
    assert text == "Đừng bỏ lỡ. Hôm nay tôi kể chuyện."


@pytest.fixture
def mock_voice():
    mock = voice.MockClient()
    voice_service.set_client(mock)
    yield mock
    voice_service.set_client(None)


def _clip(client, ready_source):  # noqa: F811
    sid = ready_source["id"]
    first = client.get(f"/sources/{sid}/moments").json()[0]
    client.patch(f"/moments/{first['id']}", json={"status": "approved"})
    return client.get(f"/sources/{sid}/clips").json()[0]["id"]


def test_voice_not_configured(client, ready_source):  # noqa: F811
    voice_service.set_client(None)
    assert client.get("/voice/status").json()["configured"] is False
    r = client.get("/voice/voices")
    assert r.status_code == 400 and r.json()["code"] == "VOICE_NOT_CONFIGURED"
    clip_id = _clip(client, ready_source)
    r = client.post(f"/clips/{clip_id}/voiceover", json={"script": "Xin chào", "voice_id": "v1", "confirm": True})
    assert r.status_code == 400 and r.json()["code"] == "VOICE_NOT_CONFIGURED"
    # the rest of the clip still works
    assert client.get(f"/clips/{clip_id}").status_code == 200


def test_voiceover_flow_confirm_cache_and_audio(client, ready_source, mock_voice):  # noqa: F811
    status = client.get("/voice/status").json()
    assert status["configured"] and status["mock"] and status["subscription"]["character_limit"] == 10000
    voices = client.get("/voice/voices").json()
    assert {v["voice_id"] for v in voices} == {"mock_vi_linh", "mock_en_adam"}
    assert client.get(voices[0]["preview_url"]).status_code == 200

    clip_id = _clip(client, ready_source)
    body = {"script": "Đừng bỏ lỡ điều này nhé", "voice_id": "mock_vi_linh", "settings": {"stability": 0.4, "speed": 1.0}}
    est = client.post(f"/clips/{clip_id}/voiceover/estimate", json=body).json()
    assert est["characters"] == 23 and est["credits"] == 12 and est["cached"] is False and est["credits_to_spend"] == 12

    # no confirmation -> nothing spent
    r = client.post(f"/clips/{clip_id}/voiceover", json=body)
    assert r.status_code == 409 and r.json()["code"] == "VOICE_CONFIRM_REQUIRED"
    assert r.json()["params"] == {"characters": 23, "credits": 12}
    assert not [c for c in mock_voice.calls if c[0] == "tts"]

    r = client.post(f"/clips/{clip_id}/voiceover", json={**body, "confirm": True})
    assert r.status_code == 200, r.text
    res = r.json()
    vo = res["clip"]["edl"]["voiceover"]
    assert res["cached"] is False and res["credits"] == 12
    assert vo["enabled"] and vo["audio_path"].startswith("voice_cache/tts_") and vo["duration"] > 0.5
    assert vo["settings"]["stability"] == 0.4 and vo["model_id"] == "eleven_flash_v2_5"
    audio = client.get(f"/voice/audio/{vo['audio_path'].split('/')[1]}")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"

    # regenerate the same script: cached, free, no API call (even without confirm)
    again = client.post(f"/clips/{clip_id}/voiceover", json=body).json()
    assert again["cached"] is True and again["credits"] == 0
    assert len([c for c in mock_voice.calls if c[0] == "tts"]) == 1
    assert client.post(f"/clips/{clip_id}/voiceover/estimate", json=body).json()["credits_to_spend"] == 0

    # the EDL can only point at voice cache files
    bad = {**vo, "audio_path": "../../.env"}
    assert client.patch(f"/clips/{clip_id}", json={"edl": {"voiceover": bad}}).status_code == 422
    assert client.get("/voice/audio/..%2F..%2F.env").status_code == 404


def test_voice_errors_are_translated_codes(client, ready_source):  # noqa: F811
    poor = voice.MockClient(limit=5)
    voice_service.set_client(poor)
    try:
        clip_id = _clip(client, ready_source)
        r = client.post(f"/clips/{clip_id}/voiceover", json={"script": "a long enough script", "voice_id": "mock_en_adam", "confirm": True})
        assert r.status_code == 402 and r.json()["code"] == "VOICE_QUOTA_EXCEEDED"
        r = client.post(f"/clips/{clip_id}/voiceover", json={"script": "hi", "voice_id": "nope", "confirm": True})
        assert r.status_code == 404 and r.json()["code"] == "VOICE_NOT_FOUND"
        assert client.post(f"/clips/{clip_id}/voiceover", json={"script": "  ", "voice_id": "mock_en_adam"}).json()["code"] == "VOICE_SCRIPT_EMPTY"
        r = client.post(f"/clips/{clip_id}/voiceover", json={"script": "x" * 5001, "voice_id": "mock_en_adam"})
        assert r.json()["code"] == "VOICE_SCRIPT_TOO_LONG" and r.json()["params"] == {"max": 5000}
    finally:
        voice_service.set_client(None)


def test_suggest_and_isolate(client, ready_source, mock_voice):  # noqa: F811
    clip_id = _clip(client, ready_source)
    s = client.post(f"/clips/{clip_id}/voiceover/suggest").json()
    assert s["source"] == "heuristic" and s["script"]

    est = client.get(f"/clips/{clip_id}/isolate/estimate").json()
    assert est["cached"] is False and est["credits"] > 0
    assert client.post(f"/clips/{clip_id}/isolate", json={}).status_code == 409
    r = client.post(f"/clips/{clip_id}/isolate", json={"confirm": True})
    assert r.status_code == 200, r.text
    audio = r.json()["clip"]["edl"]["audio"]
    assert audio["isolate_voice"] and audio["isolated_path"].startswith("voice_cache/iso_")
    assert client.post(f"/clips/{clip_id}/isolate", json={}).json()["cached"] is True
    assert len([c for c in mock_voice.calls if c[0] == "isolate"]) == 1
