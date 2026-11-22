import threading
import time

import pytest

from app import db, errors, jobs
from app.models import JOB_CANCELLED, JOB_DONE, JOB_ERROR, JOB_QUEUED, JOB_RUNNING, Job


@pytest.fixture
def q(app_env):
    queue = jobs.JobQueue(app_env)
    yield queue
    queue.stop()


def _job(job_id):
    with db.session() as s:
        return s.get(Job, job_id)


def test_enqueue_and_run_success_with_progress_and_log(q):
    seen = {}

    def handler(ctx):
        seen["params"] = ctx.params
        ctx.progress(0.5, step="half", force=True)
        seen["mid"] = _job(ctx.job_id).progress
        ctx.log("hello log")

    q.register("demo", handler)
    job = q.enqueue("demo", params={"x": 1})
    assert job.status == JOB_QUEUED
    done = q.run_next()
    assert done.status == JOB_DONE and done.progress == 1.0 and done.attempts == 1
    assert seen == {"params": {"x": 1}, "mid": 0.5}
    assert "hello log" in q.read_log(job.id)
    assert q.run_next() is None


def test_unknown_kind_is_rejected(q):
    with pytest.raises(ValueError):
        q.enqueue("nope")


def test_app_error_and_crash_are_recorded(q):
    def app_error(ctx):
        raise errors.AppError(errors.PROXY_FAILED)

    def crash(ctx):
        raise ZeroDivisionError("boom")

    q.register("a", app_error)
    q.register("b", crash)
    ja, jb = q.enqueue("a"), q.enqueue("b")
    q.run_all()
    assert _job(ja.id).status == JOB_ERROR and _job(ja.id).error["code"] == "PROXY_FAILED"
    assert _job(jb.id).error["code"] == "JOB_FAILED"
    assert "ZeroDivisionError" in q.read_log(jb.id)


def test_disk_full_maps_to_disk_full(q):
    def full(ctx):
        raise OSError(28, "No space left on device")

    q.register("full", full)
    job = q.enqueue("full")
    q.run_next()
    assert _job(job.id).error["code"] == "DISK_FULL"


def test_cancel_queued_job(q):
    q.register("demo", lambda ctx: None)
    job = q.enqueue("demo")
    out = q.cancel(job.id)
    assert out.status == JOB_CANCELLED
    assert q.run_next() is None


def test_cancel_running_job_from_another_thread(q):
    started = threading.Event()

    def slow(ctx):
        started.set()
        for _ in range(200):
            ctx.check_cancelled()
            time.sleep(0.01)

    q.register("slow", slow)
    job = q.enqueue("slow")
    t = threading.Thread(target=q.run_next)
    t.start()
    assert started.wait(5)
    q.cancel(job.id)
    t.join(5)
    row = _job(job.id)
    assert row.status == JOB_CANCELLED and row.error["code"] == "JOB_CANCELLED"


def test_cancel_kills_attached_process(q):
    import subprocess
    import sys

    started = threading.Event()
    result = {}

    def with_child(ctx):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        ctx.attach_process(proc)
        started.set()
        result["rc"] = proc.wait(timeout=20)

    q.register("child", with_child)
    job = q.enqueue("child")
    t = threading.Thread(target=q.run_next)
    t.start()
    assert started.wait(10)
    q.cancel(job.id)
    t.join(20)
    assert result["rc"] != 0
    assert _job(job.id).status == JOB_CANCELLED


def test_recover_requeues_running_jobs_and_removes_temp_files(q, app_env):
    q.register("demo", lambda ctx: None)
    job = q.enqueue("demo")
    with db.session() as s:
        row = s.get(Job, job.id)
        row.status = JOB_RUNNING  # as if claimed, then the app was closed
        row.attempts = 1
        s.add(row)
        s.commit()
    folder = app_env / "src_deadbeef"
    folder.mkdir(parents=True)
    (folder / "proxy.mp4.tmp").write_bytes(b"x")
    (folder / "original.mp4.part").write_bytes(b"x")
    (folder / "keep.mp4").write_bytes(b"x")
    models = app_env / "models"
    models.mkdir()
    (models / "weights.tmp").write_bytes(b"x")  # model downloads are never touched

    assert q.recover() == 1
    assert _job(job.id).status == JOB_QUEUED
    assert not (folder / "proxy.mp4.tmp").exists() and not (folder / "original.mp4.part").exists()
    assert (folder / "keep.mp4").exists() and (models / "weights.tmp").exists()
    assert q.run_next().status == JOB_DONE
    assert _job(job.id).attempts == 2


def test_lanes_are_independent(q):
    q.register("demo", lambda ctx: None)
    main_job = q.enqueue("demo")
    dl_job = q.enqueue("demo", lane="download")
    assert q.run_next("download").id == dl_job.id
    assert q.run_next("download") is None
    assert q.run_next("main").id == main_job.id


def test_workers_process_jobs_in_background(q):
    done = threading.Event()
    q.register("demo", lambda ctx: done.set())
    q.start()
    q.enqueue("demo")
    assert done.wait(5)
