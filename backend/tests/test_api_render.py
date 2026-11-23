"""Render / export API end to end on a tiny generated source (FFmpeg renderer, Remotion disabled for
speed): render -> outputs + render.json -> CapCut bundle -> file endpoints -> import of an edited
version -> batch + cancel -> startup reconcile."""

import json
import zipfile

import pytest

from app import clips_service, moments_service, render_service
from pipeline import render
from tests.conftest import make_media, needs_ffmpeg
from tests.test_api_moments import doc, waiting_source, write_result  # noqa: F401 - fixtures
from tests.test_api_sources import client, fake_transcriber, run_queue  # noqa: F401 - fixtures

pytestmark = needs_ffmpeg


@pytest.fixture
def prepared(client, waiting_source, monkeypatch):  # noqa: F811
    monkeypatch.setattr(render, "remotion_available", lambda: False)
    sid = waiting_source["id"]
    write_result(sid, doc(sid, (0.1, 1.1, "Chào"), (1.5, 2.9, "Cả đoạn")))
    moments_service.ResultWatcher().tick()
    moments = client.get(f"/sources/{sid}/moments").json()
    for m in moments:
        client.patch(f"/moments/{m['id']}", json={"status": "approved"})
    run_queue("edit")
    clips = client.get(f"/sources/{sid}/clips").json()
    assert len(clips) == 2 and all(c["work"]["prepare"]["state"] == "done" for c in clips)
    return sid, [c["id"] for c in clips]


def test_render_capcut_files_and_import(client, prepared, tmp_path):  # noqa: F811
    sid, (cid, other) = prepared
    assert set(client.get("/render/presets").json()["presets"]) == {"youtube_shorts", "tiktok", "reels"}
    assert "encoders" in client.get("/render/encoders").json()
    # nothing rendered yet: CapCut needs the final, files are 404
    assert client.post(f"/clips/{cid}/capcut").json()["code"] == "RENDER_NOT_FOUND"
    assert client.get(f"/clips/{cid}/render/file?kind=final").status_code == 404

    queued = client.post(f"/clips/{cid}/render", json={"renderer": "ffmpeg"}).json()
    assert queued["render"]["state"] == "queued" and queued["status"] == "approved"
    assert client.post(f"/clips/{cid}/render", json={}).json()["code"] == "RENDER_BUSY"
    assert run_queue("render") == 1

    out = client.get(f"/clips/{cid}/export").json()
    assert out["render"]["state"] == "done", out["render"]
    assert out["status"] == "rendered" and out["files"]["final"]["size"] > 0
    assert out["files"]["srt"] and out["files"]["thumb"] and out["files"]["bundle"] is None
    info = out["render"]["info"]
    assert info["renderer"] == "ffmpeg" and (info["width"], info["height"]) == (1080, 1920)
    folder = render_service.out_dir(clips_service._load(cid))
    meta = json.loads((folder / "render.json").read_text("utf-8"))
    assert meta["renderer"] == "ffmpeg" and meta["rendered_at"] and meta == info
    assert not render_service.work_dir(clips_service._load(cid)).exists()  # intermediates removed
    assert [e["id"] for e in client.get("/exports").json()] == [cid]  # the other clip is still a draft

    r = client.get(f"/clips/{cid}/render/file?kind=final&download=true")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert "attachment" in r.headers["content-disposition"]
    assert client.get(f"/clips/{cid}/render/file?kind=srt").text.splitlines()[:3] == ["1", "00:00:00,000 --> 00:00:00,900", "Xin chào các"]
    assert client.get(f"/clips/{cid}/render/file?kind=nope").status_code in (404, 422)

    # CapCut bundle
    assert client.post(f"/clips/{cid}/capcut").json()["capcut"]["state"] == "queued"
    run_queue("render")
    out = client.get(f"/clips/{cid}/export").json()
    assert out["capcut"]["state"] == "done", out["capcut"]
    with zipfile.ZipFile(folder / render_service.BUNDLE_ZIP) as z:
        names = {n.split("/")[-1] for n in z.namelist()}
    assert {"clip_9x16_clean.mp4", "clip_final.mp4", "captions.srt", "original_audio.wav", "README.txt"} <= names
    readme = (folder / "capcut" / "README.txt").read_text("utf-8-sig")
    assert "ENGLISH" in readme and "TIẾNG VIỆT" in readme

    # import an edited version from CapCut: an invalid file is refused, a video is kept
    bad = client.post(f"/clips/{cid}/import?name=notes.mp4", content=b"not a video")
    assert bad.status_code == 400 and bad.json()["code"] == "IMPORT_INVALID_FILE"
    edited = make_media(tmp_path / "edited.mp4", seconds=1, size="540x960")
    ok = client.post(f"/clips/{cid}/import?name=Bản sửa.mp4", content=edited.read_bytes())
    assert ok.status_code == 200, ok.text
    assert ok.json()["files"]["imported"]["size"] == edited.stat().st_size
    assert (folder / render_service.IMPORTED_NAME).exists()
    assert not list(folder.glob("*.part"))


def test_batch_cancel_and_reconcile(client, prepared):  # noqa: F811
    sid, (a, b) = prepared
    res = client.post("/renders/batch", json={"clip_ids": [a, b, "clip_nope"], "renderer": "ffmpeg"}).json()
    assert res["queued"] == [a, b] and res["skipped"][0]["clip_id"] == "clip_nope"

    # cancelling a queued render: the job never runs
    assert client.post(f"/clips/{b}/render/cancel").json()["render"]["state"] == "cancelled"
    assert run_queue("render") == 1
    assert client.get(f"/clips/{a}/export").json()["render"]["state"] == "done"
    assert client.get(f"/clips/{b}/export").json()["render"]["state"] == "cancelled"

    # a crash mid-render (state running, no job, leftovers on disk) is cleaned up at startup
    clip = clips_service._load(b)
    render_service._set_task(b, "render", state="running", job_id=999999)
    wd = render_service.work_dir(clip)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "render_src.mp4").write_bytes(b"x")
    out = render_service.out_dir(clip)
    out.mkdir(parents=True, exist_ok=True)
    (out / "clip_final.mp4.part").write_bytes(b"x")
    assert render_service.reconcile() == 1
    assert client.get(f"/clips/{b}/export").json()["render"]["state"] == "cancelled"
    assert not wd.exists() and not (out / "clip_final.mp4.part").exists()
    assert render_service.reconcile() == 0
