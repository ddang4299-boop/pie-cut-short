import re

import pytest
from fastapi.testclient import TestClient

from app import errors, main


def test_registry_codes_are_upper_snake_and_unique():
    assert len(errors.REGISTRY) == len(set(errors.REGISTRY))
    for code, entry in errors.REGISTRY.items():
        assert re.fullmatch(r"[A-Z][A-Z0-9_]+", code)
        assert entry.code == code
        assert 400 <= entry.http_status <= 599


def test_issue_checks_params():
    assert errors.issue(errors.DISK_SPACE_LOW, needed_gb=12, free_gb=3.5) == {
        "code": "DISK_SPACE_LOW",
        "severity": "error",
        "params": {"needed_gb": 12, "free_gb": 3.5},
    }
    with pytest.raises(ValueError):
        errors.issue(errors.DISK_SPACE_LOW, needed_gb=12)
    with pytest.raises(ValueError):
        errors.issue(errors.NO_AUDIO_STREAM, extra=1)


def test_app_error_payload():
    exc = errors.AppError(errors.FILE_NOT_FOUND, name="a.mp4")
    assert exc.http_status == 404
    assert exc.to_dict()["params"] == {"name": "a.mp4"}


@pytest.fixture
def client(monkeypatch):
    main._cached_system_info.cache_clear()
    yield TestClient(main.app)
    main._cached_system_info.cache_clear()


def fake_info(ffmpeg_ok=True, device="cuda"):
    from pipeline import preflight

    ff = preflight.ToolInfo(ffmpeg_ok, "9.0.2" if ffmpeg_ok else None, "/bin/ffmpeg" if ffmpeg_ok else None)
    return {
        "ffmpeg": vars(ff),
        "ffprobe": vars(ff),
        "device": device,
        "gpu": {"vendor": "nvidia", "name": "Test GPU", "memory_mb": 8192} if device == "cuda" else None,
        "encoder": "h264_nvenc" if device == "cuda" else "libx264",
        "issues": preflight.machine_issues(ff, ff, device),
    }


def test_health_ok(client, monkeypatch):
    monkeypatch.setattr(main.preflight, "system_info", lambda *a: fake_info())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ffmpeg"]["version"] == "9.0.2"
    assert body["device"] == "cuda"
    assert body["issues"] == []


def test_health_degraded_without_ffmpeg(client, monkeypatch):
    monkeypatch.setattr(main.preflight, "system_info", lambda *a: fake_info(ffmpeg_ok=False, device="cpu"))
    body = client.get("/health?refresh=true").json()
    assert body["status"] == "degraded"
    assert [i["code"] for i in body["issues"]] == ["FFMPEG_NOT_FOUND", "FFPROBE_NOT_FOUND", "GPU_NOT_AVAILABLE"]


def test_health_real_machine(client):
    """Runs the real detection (ffmpeg may or may not be installed)."""
    body = client.get("/health").json()
    assert body["device"] in ("cuda", "mps", "cpu")
    assert body["status"] in ("ok", "degraded")


def test_unknown_route_returns_code(client):
    r = client.get("/no-such-route")
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_validation_error_returns_code(client, monkeypatch):
    monkeypatch.setattr(main.preflight, "system_info", lambda *a: fake_info())
    r = client.get("/health?refresh=notabool")
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["params"]["fields"] == "refresh"
