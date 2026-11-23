"""Phase 7: per-channel presets, batch approve/reject, new settings (whisper model, device), cache
summary + cleanup."""

import pytest

from app import channels, clips_service, tasks
from tests.conftest import needs_ffmpeg
from tests.test_api_clips import ready_source  # noqa: F401 - fixture
from tests.test_api_moments import doc, waiting_source, write_result  # noqa: F401 - fixtures
from tests.test_api_sources import client, fake_transcriber, run_queue  # noqa: F401 - fixtures

CHANNEL = {"name": " Kênh TikTok ", "platform": "tiktok", "caption_style": "bounce", "caption_position_y": 0.6,
           "words_per_page": 2, "font_size": 90, "hook_enabled": True, "hook_position_y": 0.3,
           "progress_bar_enabled": False, "progress_color": "#ff3d7f", "voice_id": "voice_vi"}


def test_channel_validation_and_patch():
    out = channels.validate_list([CHANNEL, {**CHANNEL, "id": "ch_abcd1234", "name": "Second"}])
    assert out[0]["name"] == "Kênh TikTok" and out[0]["id"].startswith("ch_") and out[0]["progress_color"] == "#FF3D7F"
    assert out[1]["id"] == "ch_abcd1234"
    # duplicate / malformed ids are replaced
    again = channels.validate_list([{**CHANNEL, "id": "ch_abcd1234"}, {**CHANNEL, "id": "ch_abcd1234"}, {**CHANNEL, "id": "x"}])
    assert len({c["id"] for c in again}) == 3 and again[0]["id"] == "ch_abcd1234"
    for bad in ([{**CHANNEL, "name": "  "}], [{**CHANNEL, "words_per_page": 9}], [{**CHANNEL, "progress_color": "red"}],
                "nope", [CHANNEL] * 31):
        with pytest.raises(Exception) as exc:
            channels.validate_list(bad)
        assert exc.value.payload["code"] == "VALIDATION_ERROR"

    edl = {"captions": {"style": "bold", "words": [{"start": 0, "end": 1, "text": "a"}]},
           "hook_text": {"enabled": False, "text": "Xin chào", "start": 0, "end": 3.5},
           "progress_bar": {"enabled": True, "color": "#FFD400"},
           "voiceover": {"enabled": True, "voice_id": "old", "audio_path": "voice_cache/tts_x.mp3", "script": "hi"}}
    patch = channels.edl_patch(edl, out[0])
    assert patch["platform"] == "tiktok"
    assert patch["captions"]["style"] == "bounce" and patch["captions"]["words"] == edl["captions"]["words"]
    assert patch["hook_text"] == {"enabled": True, "text": "Xin chào", "start": 0, "end": 3.5, "position_y": 0.3}
    assert patch["progress_bar"] == {"enabled": False, "color": "#FF3D7F"}
    vo = patch["voiceover"]
    assert vo["voice_id"] == "voice_vi" and vo["audio_path"] is None and not vo["enabled"] and vo["script"] == "hi"
    # no hook text -> the hook stays off; no channel voice -> the voiceover is untouched
    quiet = channels.edl_patch({**edl, "hook_text": {"text": ""}}, {**out[0], "voice_id": ""})
    assert quiet["hook_text"]["enabled"] is False and "voiceover" not in quiet


def test_settings_channels_whisper_and_device(client):  # noqa: F811
    s = client.get("/settings").json()
    assert s["channels"] == [] and s["whisper_model"] is None and s["device"] is None
    assert "large-v3" in s["choices"]["whisper_models"] and s["effective"]["whisper_model"] == "tiny"  # .env in tests
    r = client.patch("/settings", json={"channels": [CHANNEL], "whisper_model": "small", "device": "cpu"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["channels"][0]["name"] == "Kênh TikTok" and s["effective"] == {**s["effective"], "whisper_model": "small", "device": "cpu"}
    assert tasks.effective_whisper_model() == "small" and tasks.effective_device() == "cpu"
    assert client.patch("/settings", json={"whisper_model": "huge"}).json()["code"] == "VALIDATION_ERROR"
    assert client.patch("/settings", json={"device": "tpu"}).json()["code"] == "VALIDATION_ERROR"
    bad = client.patch("/settings", json={"channels": [{**CHANNEL, "caption_style": "wavy"}]}).json()
    assert bad["code"] == "VALIDATION_ERROR" and "channels.0" in bad["params"]["fields"]
    # back to .env defaults
    s = client.patch("/settings", json={"whisper_model": None, "device": None}).json()
    assert s["effective"]["whisper_model"] == "tiny"


@needs_ffmpeg
def test_batch_approve_with_channel_and_apply_to_clip(client, ready_source):  # noqa: F811
    sid = ready_source["id"]
    ch = client.patch("/settings", json={"channels": [CHANNEL]}).json()["channels"][0]
    first, second = client.get(f"/sources/{sid}/moments").json()

    res = client.post(f"/sources/{sid}/moments/batch", json={"ids": [first["id"], second["id"], 99999],
                                                            "status": "approved", "channel_id": ch["id"]}).json()
    assert res["updated"] == [first["id"], second["id"]] and res["skipped"] == [99999]
    assert {m["status"] for m in res["moments"]} == {"approved"}
    assert {(m["caption_style"], m["platform"]) for m in res["moments"]} == {("bounce", "tiktok")}
    clips = client.get(f"/sources/{sid}/clips").json()
    assert len(clips) == 2
    for c in clips:
        full = client.get(f"/clips/{c['id']}").json()
        assert full["edl"]["captions"]["position_y"] == 0.6 and full["edl"]["captions"]["words_per_page"] == 2
        assert full["edl"]["progress_bar"]["enabled"] is False and full["edl"]["voiceover"]["voice_id"] == "voice_vi"
        assert full["work"]["channel_id"] == ch["id"]
    run_queue("edit")  # the clip jobs still run after the channel was applied
    assert clips_service._load(clips[0]["id"]).edl["captions"]["style"] == "bounce"

    # rejecting in batch; unknown channel / bad status
    res = client.post(f"/sources/{sid}/moments/batch", json={"ids": [second["id"]], "status": "rejected"}).json()
    assert [m["status"] for m in res["moments"] if m["id"] == second["id"]] == ["rejected"]
    assert client.post(f"/sources/{sid}/moments/batch", json={"ids": [first["id"]], "status": "approved",
                                                             "channel_id": "ch_nope"}).json()["code"] == "CHANNEL_NOT_FOUND"
    assert client.post(f"/sources/{sid}/moments/batch", json={"ids": [first["id"]], "status": "done"}).json()["code"] == "VALIDATION_ERROR"

    # apply a channel to one clip from the editor
    other = client.patch("/settings", json={"channels": [ch, {**CHANNEL, "name": "Reels", "platform": "reels",
                                                              "caption_style": "clean", "voice_id": ""}]}).json()["channels"][1]
    out = client.post(f"/clips/{clips[0]['id']}/channel", json={"channel_id": other["id"]}).json()
    assert out["edl"]["platform"] == "reels" and out["edl"]["captions"]["style"] == "clean"
    assert out["edl"]["voiceover"]["voice_id"] == "voice_vi"  # a channel without a voice keeps the clip's
    assert client.post(f"/clips/{clips[0]['id']}/channel", json={"channel_id": "ch_nope"}).json()["code"] == "CHANNEL_NOT_FOUND"


def test_cache_summary_and_clear(client, app_env):  # noqa: F811
    (app_env / "cache" / "transcripts").mkdir(parents=True)
    (app_env / "cache" / "transcripts" / "a.json").write_bytes(b"x" * 100)
    (app_env / "voice_cache").mkdir()
    (app_env / "voice_cache" / "preview_v1.mp3").write_bytes(b"x" * 10)
    (app_env / "voice_cache" / "tts_0123.mp3").write_bytes(b"x" * 50)  # paid: never cleared
    (app_env / "logs").mkdir(exist_ok=True)
    (app_env / "logs" / "job_9999.log").write_text("old")

    items = {i["key"]: i for i in client.get("/cache").json()["items"]}
    assert items["transcripts"]["bytes"] == 100 and items["voice_previews"]["bytes"] == 10
    assert items["voice_generated"] == {"key": "voice_generated", "bytes": 50, "files": 1, "clearable": False}
    assert items["logs"]["files"] >= 1

    out = client.post("/cache/clear", json={"keys": ["transcripts", "voice_generated"]}).json()
    assert out["cleared"] == ["transcripts"] and out["freed_bytes"] == 100
    assert (app_env / "voice_cache" / "tts_0123.mp3").exists()
    out = client.post("/cache/clear", json={}).json()
    assert set(out["cleared"]) == {"transcripts", "remotion_bundle", "logs", "voice_previews"}
    assert not (app_env / "voice_cache" / "preview_v1.mp3").exists() and not (app_env / "logs" / "job_9999.log").exists()
    assert (app_env / "voice_cache" / "tts_0123.mp3").exists()
