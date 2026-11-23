"""Clips API: approving a moment creates a clip -> the clip job rough-cuts and reframes -> EDL edits
are validated and persisted -> the clip follows its moment's range -> deletion."""

import pytest

from app import clips_service, edl as edl_mod, moments_service
from tests.conftest import needs_ffmpeg
from tests.test_api_moments import doc, waiting_source, write_result  # noqa: F401 - fixtures
from tests.test_api_sources import client, fake_transcriber, run_queue  # noqa: F401 - fixtures

pytestmark = needs_ffmpeg


@pytest.fixture
def ready_source(client, waiting_source):  # noqa: F811
    sid = waiting_source["id"]
    write_result(sid, doc(sid, (0.1, 1.1, "Chào"), (1.5, 2.9, "Cả đoạn")))
    moments_service.ResultWatcher().tick()
    return client.get(f"/sources/{sid}").json()


def test_edl_defaults_and_validation():
    data = edl_mod.build_default(clip_id="clip_x", source_id="src_x", moment={
        "id": 1, "start": 1.0, "end": 9.0, "hook": " Xin chào ", "caption_style": "clean", "platform": "tiktok"},
        mode="cursor")
    assert data["segments"] == [{"start": 1.0, "end": 9.0}]
    assert data["crop"]["mode"] == "cursor" and data["crop"]["keyframes"] == [{"t": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0}]
    assert data["captions"]["style"] == "clean" and data["hook_text"] == {
        "enabled": True, "text": "Xin chào", "start": 0.0, "end": 3.5, "position_y": 0.18}
    assert data["output"] == {"w": 1080, "h": 1920, "fps": 30} and data["status"] == "draft"
    # keyframes are sorted and de-duplicated; unknown keys dropped
    data["crop"]["keyframes"] = [{"t": 2, "x": 0.1, "y": 0.5}, {"t": 0, "x": 0.9, "y": 0.5}, {"t": 2, "x": 0.2, "y": 0.5}]
    data["junk"] = 1
    out = edl_mod.validate(data)
    assert [k["t"] for k in out["crop"]["keyframes"]] == [0, 2] and out["crop"]["keyframes"][1]["x"] == 0.2
    assert "junk" not in out
    for bad in ({"segments": [{"start": 1, "end": 1.1}]},
                {"segments": [{"start": 1, "end": 3}, {"start": 2, "end": 4}]},
                {"crop": {"keyframes": [{"t": 0, "x": 1.5, "y": 0.5}]}},
                {"progress_bar": {"color": "yellow"}},
                {"crop": {"mode": "split", "split": {"top": [0.8, 0, 0.5, 0.5], "bottom": [0, 0, 1, 1]}}}):
        with pytest.raises(Exception) as exc:
            edl_mod.validate({**data, **bad})
        assert getattr(exc.value, "payload", {}).get("code") == "VALIDATION_ERROR"
    with pytest.raises(Exception):
        edl_mod.validate(data, source_duration=5.0)


def test_approving_creates_a_clip_and_the_job_fills_it(client, ready_source):  # noqa: F811
    sid = ready_source["id"]
    first, second = client.get(f"/sources/{sid}/moments").json()
    assert client.get(f"/sources/{sid}/clips").json() == []

    client.patch(f"/moments/{first['id']}", json={"status": "approved"})
    clips = client.get(f"/sources/{sid}/clips").json()
    assert len(clips) == 1 and clips[0]["moment_id"] == first["id"] and clips[0]["title"] == "Chào"
    assert clips[0]["work"]["prepare"]["state"] == "queued" and clips[0]["busy"]
    # approving again does not create a second clip; the source is not "busy" because of it
    client.patch(f"/moments/{first['id']}", json={"status": "approved"})
    assert len(client.get(f"/sources/{sid}/clips").json()) == 1
    assert client.get(f"/sources/{sid}").json()["active_job"] is None

    assert run_queue("edit") == 1
    clip = client.get(f"/clips/{clips[0]['id']}").json()
    assert clip["work"]["prepare"]["state"] == "done", clip["work"]
    assert clip["work"]["reframe"]["state"] == "done", clip["work"]
    assert clip["work"]["reframe"]["info"]["applied"] is True
    assert (clips_service.clip_dir(clips_service._load(clip["id"])) / "rough.mp4").exists()
    edl = clip["edl"]
    assert edl["segments"] == [{"start": first["start"], "end": first["end"]}]
    assert edl["crop"]["keyframes"] and edl["moment_id"] == first["id"] and not clip["busy"]

    # edit framing: a manual keyframe; the change is validated and persisted
    crop = {**edl["crop"], "mode": "manual", "keyframes": [{"t": 0, "x": 0.3, "y": 0.5, "zoom": 1.2},
                                                            {"t": 0.5, "x": 0.6, "y": 0.5, "zoom": 1.2}]}
    r = client.patch(f"/clips/{clip['id']}", json={"edl": {"crop": crop, "audio": {**edl["audio"], "volume_db": -3}}})
    assert r.status_code == 200, r.text
    saved = client.get(f"/clips/{clip['id']}").json()
    assert saved["edl"]["crop"]["keyframes"][1]["x"] == 0.6 and saved["edl"]["audio"]["volume_db"] == -3
    assert saved["work"]["crop_user_edited"] is True
    bad = client.patch(f"/clips/{clip['id']}", json={"edl": {"crop": {**crop, "keyframes": [{"t": 0, "x": 2, "y": 0}]}}})
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_ERROR"
    assert client.patch(f"/clips/{clip['id']}", json={"status": "review"}).json()["status"] == "review"
    assert client.patch(f"/clips/{clip['id']}", json={"status": "nope"}).json()["code"] == "VALIDATION_ERROR"

    # the moment's range changes -> the single-segment clip follows; manual framing is kept
    moved = client.patch(f"/moments/{first['id']}", json={"start": 0.0, "end": 1.4, "snap": False}).json()
    after = client.get(f"/clips/{clip['id']}").json()
    assert after["edl"]["segments"] == [{"start": moved["start"], "end": moved["end"]}]
    assert after["work"]["prepare"]["state"] == "queued" and after["work"]["reframe"]["state"] == "done"
    run_queue("edit")
    assert client.get(f"/clips/{clip['id']}").json()["edl"]["crop"]["keyframes"][0]["x"] == 0.3

    # recompute overwrites the manual framing
    assert client.post(f"/clips/{clip['id']}/reframe", json={"mode": "auto"}).json()["busy"]
    assert client.post(f"/clips/{clip['id']}/reframe", json={}).json()["code"] == "CLIP_BUSY"
    run_queue("edit")
    recomputed = client.get(f"/clips/{clip['id']}").json()
    assert recomputed["edl"]["crop"]["mode"] == "auto" and not recomputed["work"]["crop_user_edited"]
    assert client.post(f"/clips/{clip['id']}/reframe", json={"mode": "zoomy"}).json()["code"] == "VALIDATION_ERROR"

    # opening a moment's clip from the editor creates it on demand
    opened = client.post(f"/moments/{second['id']}/clip").json()
    assert opened["moment_id"] == second["id"] and opened["edl"]["segments"][0]["start"] == second["start"]
    assert client.post("/moments/99999/clip").json()["code"] == "MOMENT_NOT_FOUND"
    assert client.get("/clips/clip_nope").status_code == 404

    # deleting the moment deletes its clip
    client.delete(f"/moments/{second['id']}")
    assert [c["moment_id"] for c in client.get(f"/sources/{sid}/clips").json()] == [first["id"]]


def test_manual_moment_gets_a_clip(client, ready_source):  # noqa: F811
    sid = ready_source["id"]
    m = client.post(f"/sources/{sid}/moments", json={"start": 0.2, "end": 2.0, "title": "Tự chọn"}).json()
    assert [c["moment_id"] for c in client.get(f"/sources/{sid}/clips").json()] == [m["id"]]


def test_user_edit_during_reframe_is_kept(client, ready_source, monkeypatch):  # noqa: F811
    sid = ready_source["id"]
    first = client.get(f"/sources/{sid}/moments").json()[0]
    client.patch(f"/moments/{first['id']}", json={"status": "approved"})
    clip = client.get(f"/sources/{sid}/clips").json()[0]
    full = client.get(f"/clips/{clip['id']}").json()
    real = clips_service._apply_reframe

    def edit_then_apply(clip_id, result, start_edits):
        crop = {**full["edl"]["crop"], "keyframes": [{"t": 0, "x": 0.11, "y": 0.5, "zoom": 1}]}
        clips_service.patch_clip(clip_id, edl_patch={"crop": crop})
        real(clip_id, result, start_edits)

    monkeypatch.setattr(clips_service, "_apply_reframe", edit_then_apply)
    run_queue("edit")
    after = client.get(f"/clips/{clip['id']}").json()
    assert after["edl"]["crop"]["keyframes"][0]["x"] == 0.11
    assert after["work"]["reframe"]["info"]["applied"] is False


def test_reconcile_resets_stuck_work(client, ready_source):  # noqa: F811
    sid = ready_source["id"]
    first = client.get(f"/sources/{sid}/moments").json()[0]
    client.patch(f"/moments/{first['id']}", json={"status": "approved"})
    clip_id = client.get(f"/sources/{sid}/clips").json()[0]["id"]
    from app import db
    from app.models import Job
    from sqlmodel import select

    with db.session() as s:  # simulate a crash: the job row is gone, the clip still says queued
        for job in s.exec(select(Job).where(Job.kind == "clip")).all():
            s.delete(job)
        s.commit()
    assert clips_service.reconcile() == 1
    assert client.get(f"/clips/{clip_id}").json()["work"]["prepare"]["state"] == "pending"
