"""End-to-end API tests: upload/import -> preflight -> Start -> proxy -> transcript, with a fake
transcriber (no whisper model needed). Job workers are off; tests run the queue by hand."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, jobs, main, tasks
from app.models import JOB_QUEUED, JOB_RUNNING, Job, Source
from pipeline import transcribe
from tests.conftest import make_media, needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def client(app_env):
    main._cached_system_info.cache_clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def fake_transcriber(monkeypatch):
    calls = []

    def fake(ctx, audio, out, *, model, device, language, report):
        calls.append({"audio": Path(audio), "model": model, "device": device, "language": language})
        report(0.5)
        segments = [{"start": 0.0, "end": 1.2, "text": "Xin chào các bạn.", "words": [
            {"start": 0.0, "end": 0.3, "text": " Xin"}, {"start": 0.3, "end": 0.6, "text": " chào"},
            {"start": 0.6, "end": 0.9, "text": " các"}, {"start": 0.9, "end": 1.2, "text": " bạn."}]}]
        data = transcribe.build_transcript(segments, language="vi" if language == "auto" else language,
                                           language_option=language, language_probability=0.9,
                                           model=model, device=device, duration=2.0)
        Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(tasks, "run_transcriber", fake)
    return calls


def upload(client, path: Path, name: str | None = None):
    return client.post("/sources/upload", params={"name": name or path.name}, content=path.read_bytes())


def run_queue(lane="main"):
    return jobs.get_queue().run_all(lane)


def test_upload_runs_preflight_and_waits_for_start(client, tmp_path):
    media = make_media(tmp_path / "clip.mp4", seconds=2)
    r = upload(client, media, name="Phỏng vấn.mp4")
    assert r.status_code == 200, r.text
    src = r.json()
    assert src["name"] == "Phỏng vấn.mp4" and src["origin"] == "file"
    assert src["status"] == "new" and src["can_start"] is True
    assert src["preflight"]["ok"] is True
    assert src["steps"]["preflight"]["state"] == "done"
    assert src["steps"]["proxy"]["state"] == "pending"
    assert (src["width"], src["height"]) == (1280, 720) and src["has_audio"] is True
    assert src["thumbnail_url"]
    assert client.get(src["thumbnail_url"]).headers["content-type"] == "image/jpeg"
    # nothing runs until Start
    assert client.get("/jobs").json() == []


def test_upload_rejects_unsupported_type(client):
    r = client.post("/sources/upload", params={"name": "notes.txt"}, content=b"hello")
    assert r.status_code == 415 and r.json()["code"] == "UNSUPPORTED_FILE_TYPE"


def test_upload_of_non_video_reports_preflight_error(client):
    r = client.post("/sources/upload", params={"name": "broken.mp4"}, content=b"not really a video" * 100)
    assert r.status_code == 200
    src = r.json()
    assert src["preflight"]["ok"] is False and src["can_start"] is False
    assert src["steps"]["preflight"]["state"] == "error"
    assert src["steps"]["preflight"]["error"]["code"] == "PROBE_FAILED"
    r = client.post(f"/sources/{src['id']}/start", json={})
    assert r.status_code == 409 and r.json()["code"] == "SOURCE_NOT_STARTABLE"


def test_full_pipeline_with_cache_and_rerun(client, tmp_path, fake_transcriber):
    media = make_media(tmp_path / "talk.mp4", seconds=3, size="1920x1080")
    src = upload(client, media).json()
    r = client.post(f"/sources/{src['id']}/start", json={"language": "auto", "notes": "focus on tips"})
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["status"] == "processing" and started["active_job"]["status"] == JOB_QUEUED
    assert client.post(f"/sources/{src['id']}/start", json={}).json()["code"] == "SOURCE_BUSY"

    assert run_queue() == 1
    done = client.get(f"/sources/{src['id']}").json()
    assert done["status"] == "waiting", done  # default provider claude_code waits for /find-moments
    assert done["notes"] == "focus on tips"
    for step in ("preflight", "proxy", "transcribe", "analyze"):
        assert done["steps"][step]["state"] == "done", step
    assert done["steps"]["moments"]["state"] == "waiting"
    assert done["has_analysis"] and done["find_moments_command"] == f"/find-moments {src['id']}"
    assert done["language"] == "vi" and done["has_transcript"]
    assert done["proxy_url"]
    proxy = client.get(done["proxy_url"])
    assert proxy.status_code == 200 and proxy.headers["content-type"] == "video/mp4"
    ranged = client.get(done["proxy_url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100

    transcript = client.get(f"/sources/{src['id']}/transcript").json()
    assert [w["text"] for w in transcript["words"]] == ["Xin", "chào", "các", "bạn."]
    assert len(fake_transcriber) == 1
    call = fake_transcriber[0]
    assert call["audio"].name == "audio_16k.wav" and call["model"] == "tiny"

    job = client.get("/jobs").json()[0]
    assert job["status"] == "done" and job["progress"] == 1.0 and job["source_name"] == "talk.mp4"
    assert "step transcribe: done" in client.get(f"/jobs/{job['id']}/log").text

    # Same file imported again -> proxy is rebuilt for the new source but the transcript is cached.
    src2 = upload(client, media, name="copy.mp4").json()
    client.post(f"/sources/{src2['id']}/start", json={})
    run_queue()
    assert client.get(f"/sources/{src2['id']}").json()["status"] == "waiting"
    assert len(fake_transcriber) == 1

    # Re-run from transcribe forces a fresh transcription; other steps stay done.
    r = client.post(f"/sources/{src['id']}/rerun", json={"from_step": "transcribe"})
    assert r.status_code == 200 and r.json()["steps"]["transcribe"]["state"] == "pending"
    assert r.json()["steps"]["proxy"]["state"] == "done"
    run_queue()
    assert len(fake_transcriber) == 2
    assert client.get(f"/sources/{src['id']}").json()["status"] == "waiting"

    # A different language option is a different cache entry.
    client.patch(f"/sources/{src['id']}", json={"language_override": "en"})
    client.post(f"/sources/{src['id']}/rerun", json={"from_step": "transcribe"})
    run_queue()
    assert fake_transcriber[-1]["language"] == "en"
    assert client.get(f"/sources/{src['id']}").json()["language"] == "en"


def test_video_without_audio_skips_transcription(client, tmp_path, fake_transcriber):
    media = make_media(tmp_path / "silent.mp4", seconds=2, audio=False)
    src = upload(client, media).json()
    assert src["has_audio"] is False
    assert any(i["code"] == "NO_AUDIO_STREAM" for i in src["preflight"]["issues"])
    client.post(f"/sources/{src['id']}/start", json={})
    run_queue()
    done = client.get(f"/sources/{src['id']}").json()
    assert done["status"] == "ready"
    assert done["steps"]["transcribe"]["state"] == "skipped"
    assert done["steps"]["transcribe"]["notices"][0]["code"] == "TRANSCRIBE_SKIPPED_NO_AUDIO"
    assert fake_transcriber == []
    assert client.get(f"/sources/{src['id']}/transcript").json()["code"] == "TRANSCRIPT_NOT_READY"


def test_transcriber_failure_marks_step_error(client, tmp_path, monkeypatch):
    from app import errors

    def failing(ctx, audio, out, **kwargs):
        raise errors.AppError(errors.TRANSCRIBE_FAILED, detail="CUDA out of memory")

    monkeypatch.setattr(tasks, "run_transcriber", failing)
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=2)).json()
    client.post(f"/sources/{src['id']}/start", json={})
    run_queue()
    out = client.get(f"/sources/{src['id']}").json()
    assert out["status"] == "error" and out["can_start"] is True
    assert out["steps"]["proxy"]["state"] == "done"
    assert out["steps"]["transcribe"]["state"] == "error"
    assert out["steps"]["transcribe"]["error"] == {
        "code": "TRANSCRIBE_FAILED", "severity": "error", "params": {"detail": "CUDA out of memory"}}


def test_resume_after_restart_skips_finished_steps(client, tmp_path, fake_transcriber, monkeypatch):
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=2)).json()
    client.post(f"/sources/{src['id']}/start", json={})
    # Simulate: proxy finished, then the app was closed while transcribing.
    real_proxy = tasks.STEP_FUNCS["proxy"]
    proxy_calls = []

    def counting_proxy(ctx, s, report):
        proxy_calls.append(1)
        return real_proxy(ctx, s, report)

    monkeypatch.setitem(tasks.STEP_FUNCS, "proxy", counting_proxy)
    monkeypatch.setitem(tasks.STEP_FUNCS, "transcribe", lambda ctx, s, report: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        jobs.get_queue().run_next()
    with db.session() as s:
        job = s.exec(__import__("sqlmodel").select(Job)).first()
        assert job.status == JOB_RUNNING  # the "crash" left it running
    monkeypatch.setitem(tasks.STEP_FUNCS, "transcribe", tasks.step_transcribe)

    assert jobs.get_queue().recover() == 1
    run_queue()
    out = client.get(f"/sources/{src['id']}").json()
    assert out["status"] == "waiting" and out["steps"]["transcribe"]["state"] == "done"
    assert len(proxy_calls) == 1  # proxy was not redone


def test_cancel_queued_job_resets_source(client, tmp_path):
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=2)).json()
    job_id = client.post(f"/sources/{src['id']}/start", json={}).json()["active_job"]["id"]
    r = client.post(f"/jobs/{job_id}/cancel")
    assert r.json()["status"] == "cancelled"
    out = client.get(f"/sources/{src['id']}").json()
    assert out["status"] == "new" and out["can_start"] is True and out["active_job"] is None


def test_import_local_path_is_used_in_place(client, tmp_path):
    media = make_media(tmp_path / "local video.mp4", seconds=2)
    r = client.post("/sources/import", json={"location": f'"{media}"'})
    assert r.status_code == 200, r.text
    src = r.json()
    assert src["origin"] == "path" and src["owns_media"] is False
    assert Path(src["media_path"]) == media.resolve() and src["preflight"]["ok"]
    # deleting with media never touches the user's own file
    r = client.delete(f"/sources/{src['id']}", params={"delete_media": True})
    assert r.json()["deleted"] is True
    assert media.exists()
    assert client.get(f"/sources/{src['id']}").json()["code"] == "SOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    ("location", "status", "code"),
    [
        ("relative/path.mp4", 400, "INVALID_LOCATION"),
        ("C:/definitely/missing/file.mp4", 404, "FILE_NOT_FOUND"),
    ],
)
def test_import_errors(client, location, status, code):
    r = client.post("/sources/import", json={"location": location})
    assert r.status_code == status and r.json()["code"] == code


def test_import_url_queues_download(client):
    r = client.post("/sources/import", json={"location": "https://www.youtube.com/watch?v=abc"})
    src = r.json()
    assert src["origin"] == "url" and src["status"] == "downloading"
    assert src["active_job"]["kind"] == "download" and src["active_job"]["lane"] == "download"
    assert src["can_start"] is False


def test_download_failure_is_reported(client, monkeypatch):
    from pipeline import ingest

    def boom(*a, **k):
        raise RuntimeError("ERROR: [youtube] abc: Video unavailable")

    monkeypatch.setattr(ingest, "download_url", boom)
    src = client.post("/sources/import", json={"location": "https://example.com/v"}).json()
    run_queue("download")
    out = client.get(f"/sources/{src['id']}").json()
    assert out["status"] == "error"
    assert out["error"] == {"code": "DOWNLOAD_FAILED", "severity": "error",
                            "params": {"detail": "[youtube] abc: Video unavailable"}}


def test_download_success_runs_preflight(client, tmp_path, monkeypatch):
    from pipeline import ingest

    def fake_download(url, dest_dir, **kwargs):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        path = make_media(Path(dest_dir) / "original.mp4", seconds=2)
        return {"path": str(path), "title": "Video hay"}

    monkeypatch.setattr(ingest, "download_url", fake_download)
    src = client.post("/sources/import", json={"location": "https://example.com/v"}).json()
    run_queue("download")
    out = client.get(f"/sources/{src['id']}").json()
    assert out["status"] == "new", out
    assert out["name"] == "Video hay" and out["can_start"]
    assert out["owns_media"] is True


def test_patch_source_validates(client, tmp_path):
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=1)).json()
    r = client.patch(f"/sources/{src['id']}", json={"content_type": "podcast", "name": "Tập 1"})
    assert r.json()["content_type"] == "podcast" and r.json()["name"] == "Tập 1"
    assert client.patch(f"/sources/{src['id']}", json={"content_type": "movie"}).status_code == 422
    assert client.patch(f"/sources/{src['id']}", json={"language_override": "fr"}).status_code == 422
    r = client.patch(f"/sources/{src['id']}", json={"content_type": None})
    assert r.json()["content_type"] is None


def test_rerun_rejects_unknown_step(client, tmp_path):
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=1)).json()
    r = client.post(f"/sources/{src['id']}/rerun", json={"from_step": "render"})
    assert r.status_code == 400 and r.json() == {"code": "INVALID_STEP", "severity": "error", "params": {"step": "render"}}


def test_delete_with_media_removes_folder(client, tmp_path, app_env):
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=1)).json()
    folder = app_env / src["id"]
    assert (folder / "original.mp4").exists()
    client.delete(f"/sources/{src['id']}", params={"delete_media": True})
    assert not folder.exists()
    with db.session() as s:
        assert s.get(Source, src["id"]) is None


def test_delete_without_media_keeps_files(client, tmp_path, app_env):
    src = upload(client, make_media(tmp_path / "a.mp4", seconds=1)).json()
    r = client.delete(f"/sources/{src['id']}")
    assert r.json() == {"id": src["id"], "deleted": True, "media_deleted": False}
    assert (app_env / src["id"] / "original.mp4").exists()


def test_unknown_source_and_job(client):
    assert client.get("/sources/src_nope").json()["code"] == "SOURCE_NOT_FOUND"
    assert client.get("/jobs/999").json()["code"] == "JOB_NOT_FOUND"


def test_reconcile_marks_stuck_sources_for_retry(app_env):
    with db.session() as s:
        stuck = Source(id="src_stuck", name="stuck", origin="path", status="processing",
                       steps={"preflight": {"state": "done", "progress": 1.0},
                              "transcribe": {"state": "running", "progress": 0.4}})
        busy = Source(id="src_busy", name="busy", origin="path", status="processing")
        s.add(stuck)
        s.add(busy)
        s.add(Job(kind="pipeline", lane="main", source_id="src_busy", status=JOB_QUEUED))
        s.commit()
    assert tasks.reconcile_sources() == 1
    stuck = tasks.load_source("src_stuck")
    assert stuck.status == "error" and stuck.error["code"] == "JOB_FAILED"
    assert stuck.steps["transcribe"]["state"] == "error"
    assert stuck.steps["preflight"]["state"] == "done"
    assert tasks.load_source("src_busy").status == "processing"
