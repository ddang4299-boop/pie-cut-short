"""Moments API: claude_code request file -> moments.json picked up by the watcher -> review edits
(approve, adjust with snapping, manual moments, delete) -> settings (weights rescore) -> re-import
keeps the user's work. Uses the fake transcriber from test_api_sources."""

import json
import os
import time

import pytest

from app import moments_service, tasks
from tests.conftest import make_media, needs_ffmpeg
from tests.test_api_sources import client, fake_transcriber, run_queue, upload  # noqa: F401 - fixtures

pytestmark = needs_ffmpeg

SCORES = {"hook_strength": 8, "coherence": 7, "emotion": 6, "value_density": 5, "payoff": 9}


@pytest.fixture
def waiting_source(client, tmp_path, fake_transcriber, monkeypatch):  # noqa: F811
    monkeypatch.setattr(moments_service, "SETTLE_SECONDS", 0.0)
    media = make_media(tmp_path / "talk.mp4", seconds=3)
    src = upload(client, media).json()
    client.post(f"/sources/{src['id']}/start", json={"notes": "keep it short"})
    run_queue()
    data = client.get(f"/sources/{src['id']}").json()
    assert data["status"] == "waiting"
    return data


def write_result(source_id: str, doc_or_text) -> None:
    path = moments_service.result_path(source_id)
    text = doc_or_text if isinstance(doc_or_text, str) else json.dumps(doc_or_text, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    time.sleep(0.02)
    stamp = time.time()  # after the request was written, different from the last checked file
    os.utime(path, (stamp, stamp))


def doc(source_id, *moments):
    return {"source_id": source_id, "summary": "Một đoạn chào hỏi.", "notes": "",
            "moments": [{"start": a, "end": b, "title": t, "hook": "", "reason": "r", "category": "story",
                         "scores": SCORES} for a, b, t in moments]}


def test_request_file_is_written_in_the_source_folder(waiting_source):
    sid = waiting_source["id"]
    request = (tasks.source_dir(sid) / "analysis_request.md").read_text("utf-8")
    assert request.startswith(f"# Analysis request: {sid}\n")
    assert "- user_notes: keep it short" in request and "Xin chào các bạn." in request
    step = waiting_source["steps"]["moments"]
    assert step["state"] == "waiting" and step["provider"] == "claude_code"
    assert waiting_source["find_moments_command"] == f"/find-moments {sid}"


def test_watcher_imports_moments_and_review_edits_persist(client, waiting_source):  # noqa: F811
    sid = waiting_source["id"]
    watcher = moments_service.ResultWatcher()
    watcher.tick()  # nothing written yet
    assert client.get(f"/sources/{sid}").json()["status"] == "waiting"

    write_result(sid, doc(sid, (0.1, 1.1, "Chào"), (1.5, 2.9, "Cả đoạn")))
    watcher.tick()
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "ready", data
    assert data["steps"]["moments"]["state"] == "done"
    assert data["steps"]["moments"]["notices"][0]["code"] == "MOMENTS_FEWER_THAN_WANTED"
    assert data["moments_summary"] == "Một đoạn chào hỏi." and data["moments_provider"] == "claude_code"
    moments = client.get(f"/sources/{sid}/moments").json()
    assert len(moments) == 2 and data["moments"] == {"approved": 0, "total": 2}
    words = client.get(f"/sources/{sid}/transcript").json()["words"]
    for m in moments:
        assert m["status"] == "proposed" and m["score"] == 71 and m["origin"] == "ai"
        for w in words:  # snapped to word boundaries
            assert not (w["start"] < m["start"] < w["end"]) and not (w["start"] < m["end"] < w["end"])

    # the watcher does not import the same file twice
    watcher.tick()
    assert len(client.get(f"/sources/{sid}/moments").json()) == 2

    first, second = moments
    r = client.patch(f"/moments/{first['id']}", json={"status": "approved", "title": "  Mở đầu  ",
                                                         "caption_style": "clean", "platform": "reels"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Mở đầu" and r.json()["caption_style"] == "clean"
    # adjust in/out: snapping moves a cut that falls inside a word ("chào" is 0.3-0.6)
    snapped = client.patch(f"/moments/{second['id']}", json={"start": 0.45, "end": 1.6}).json()
    assert not (0.3 < snapped["start"] < 0.6) and snapped["raw_start"] == 0.45
    exact = client.patch(f"/moments/{second['id']}", json={"start": 0.45, "end": 1.6, "snap": False}).json()
    assert (exact["start"], exact["end"]) == (0.45, 1.6)
    assert client.patch(f"/moments/{second['id']}", json={"status": "maybe"}).json()["code"] == "VALIDATION_ERROR"
    assert client.patch(f"/moments/{second['id']}", json={"start": 1.0, "end": 1.1}).json()["code"] == "VALIDATION_ERROR"

    # manual moment (approved by default), snap preview endpoint
    preview = client.post(f"/sources/{sid}/snap", json={"start": 0.45, "end": 1.0}).json()
    assert set(preview) == {"start", "end"}
    manual = client.post(f"/sources/{sid}/moments", json={"start": 1.5, "end": 2.8, "title": "Tự chọn"}).json()
    assert manual["status"] == "approved" and manual["origin"] == "manual" and manual["category"] == "manual"
    assert client.get(f"/sources/{sid}").json()["moments"] == {"approved": 2, "total": 3}

    # rubric weights in Settings rescore AI moments
    weights = {k: (1.0 if k == "hook_strength" else 0.0) for k in SCORES}
    settings = client.patch("/settings", json={"rubric_weights": weights}).json()
    assert settings["rubric_weights"]["hook_strength"] == 1.0
    assert {m["score"] for m in client.get(f"/sources/{sid}/moments").json() if m["origin"] == "ai"} == {80}
    assert client.patch("/settings", json={"rubric_weights": {"x": 1}}).json()["code"] == "VALIDATION_ERROR"

    # a new run replaces proposals but keeps approved + manual moments
    client.post(f"/sources/{sid}/moments/find", json={})
    run_queue()
    assert client.get(f"/sources/{sid}").json()["status"] == "waiting"
    write_result(sid, doc(sid, (0.0, 2.9, "Mới")))
    assert client.post(f"/sources/{sid}/moments/import").json()["status"] == "ready"
    after = client.get(f"/sources/{sid}/moments").json()
    titles = sorted(m["title"] for m in after)
    assert "Mở đầu" in titles and "Tự chọn" in titles and "Cả đoạn" not in titles

    # delete
    assert client.delete(f"/moments/{manual['id']}").status_code == 200
    assert client.delete(f"/moments/{manual['id']}").json()["code"] == "MOMENT_NOT_FOUND"


def test_invalid_moments_json_reports_and_keeps_waiting(client, waiting_source):  # noqa: F811
    sid = waiting_source["id"]
    write_result(sid, "{ not json")
    moments_service.ResultWatcher().tick()
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "waiting"
    assert data["steps"]["moments"]["error"]["code"] == "MOMENTS_INVALID"
    # fixing the file clears the error
    write_result(sid, doc(sid, (0.0, 2.0, "OK")))
    moments_service.ResultWatcher().tick()
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "ready" and data["steps"]["moments"]["error"] is None


def test_heuristic_provider_runs_without_waiting(client, waiting_source):  # noqa: F811
    sid = waiting_source["id"]
    client.post(f"/sources/{sid}/moments/find", json={"provider": "heuristic"})
    run_queue()
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "ready" and data["moments_provider"] == "heuristic"
    assert (tasks.source_dir(sid) / "moments.heuristic.json").exists()


def test_analysis_and_waveform_endpoints(client, waiting_source):  # noqa: F811
    sid = waiting_source["id"]
    analysis = client.get(f"/sources/{sid}/analysis").json()
    assert {"scene_cuts", "silences", "peaks", "loudness"} <= set(analysis)
    wave = client.get(f"/sources/{sid}/waveform").json()
    assert wave["rate"] == 20 and len(wave["peaks"]) > 0
    assert client.get("/sources/nope/analysis").status_code == 404
