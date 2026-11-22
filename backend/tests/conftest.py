import shutil
import subprocess
from pathlib import Path

import pytest

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def make_media(path: Path, *, video: bool = True, audio: bool = True, seconds: float = 2.0,
               size: str = "1280x720", fps: int = 30) -> Path:
    """Generate a tiny test file with ffmpeg lavfi sources."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Isolated data dir + DB for each test; job workers off (tests drive the queue)."""
    from app import db
    from app.config import get_settings

    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("START_JOB_WORKERS", "false")
    monkeypatch.setenv("WHISPER_MODEL", "tiny")
    monkeypatch.setenv("LLM_PROVIDER", "claude_code")
    get_settings.cache_clear()
    db.init_db(get_settings().db_path)
    yield data
    get_settings.cache_clear()
    db.dispose()


@pytest.fixture
def queue(app_env):
    from app import jobs, tasks

    q = jobs.JobQueue(app_env)
    tasks.register_handlers(q)
    jobs.set_queue(q)
    yield q
    jobs.set_queue(None)
