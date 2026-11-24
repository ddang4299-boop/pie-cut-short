"""Run with Claude: the app runs /find-moments headless and imports moments.json. A fake `claude`
(a small Python script) stands in for Claude Code, so no real model is called."""

import json
import sys

from app import claude_runner, jobs, moments_service
from tests.conftest import needs_ffmpeg
from tests.test_api_moments import SCORES, waiting_source  # noqa: F401 - fixture
from tests.test_api_sources import client, fake_transcriber, run_queue  # noqa: F401 - fixtures

pytestmark = needs_ffmpeg

FAKE_CLAUDE = r'''
import json, sys
out, doc, code = sys.argv[1], sys.argv[2], int(sys.argv[3])
print(json.dumps({"type": "system", "subtype": "init"}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Read", "input": {"file_path": "analysis_request.md"}}]}}))
if doc:
    open(out, "w", encoding="utf-8").write(doc)
print("not json noise")
print(json.dumps({"type": "result", "subtype": "success" if code == 0 else "error", "num_turns": 3,
                  "total_cost_usd": 0.12, "result": "done" if code == 0 else "boom"}))
sys.exit(code)
'''


def use_fake_claude(monkeypatch, tmp_path, doc: str, code: int = 0) -> None:
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, "utf-8")
    monkeypatch.setattr(claude_runner, "find_cli", lambda: sys.executable)
    monkeypatch.setattr(claude_runner, "build_command", lambda cli, sid: [
        cli, str(script), str(moments_service.result_path(sid)), doc, str(code)])


def test_run_with_claude_imports_moments(client, waiting_source, monkeypatch, tmp_path):  # noqa: F811
    sid = waiting_source["id"]
    doc = {"source_id": sid, "summary": "Tóm tắt.", "notes": "", "moments": [
        {"start": 0.1, "end": 2.9, "title": "Cả đoạn", "hook": "", "reason": "r", "category": "story", "scores": SCORES}]}
    use_fake_claude(monkeypatch, tmp_path, json.dumps(doc, ensure_ascii=False))

    r = client.post(f"/sources/{sid}/moments/claude")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "processing" and data["steps"]["moments"]["stage"] == "claude_running"
    assert data["active_job"]["kind"] == "claude_moments"
    job_id = data["active_job"]["id"]
    # a second click while it runs is refused
    assert client.post(f"/sources/{sid}/moments/claude").status_code == 409

    assert run_queue("llm") == 1
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "ready", data
    assert data["steps"]["moments"]["state"] == "done" and data["moments"]["total"] == 1
    log = jobs.get_queue().read_log(job_id)
    assert "claude: Read analysis_request.md" in log and "moments.json: imported" in log


def test_run_with_claude_failure_goes_back_to_waiting(client, waiting_source, monkeypatch, tmp_path):  # noqa: F811
    sid = waiting_source["id"]
    use_fake_claude(monkeypatch, tmp_path, "", code=1)
    client.post(f"/sources/{sid}/moments/claude")
    run_queue("llm")
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "waiting"
    error = data["steps"]["moments"]["error"]
    assert error["code"] == "CLAUDE_RUN_FAILED" and error["params"]["detail"] == "boom"


def test_run_with_claude_invalid_json_keeps_waiting(client, waiting_source, monkeypatch, tmp_path):  # noqa: F811
    sid = waiting_source["id"]
    use_fake_claude(monkeypatch, tmp_path, "{ not json")
    client.post(f"/sources/{sid}/moments/claude")
    run_queue("llm")
    data = client.get(f"/sources/{sid}").json()
    assert data["status"] == "waiting" and data["steps"]["moments"]["error"]["code"] == "MOMENTS_INVALID"


def test_run_with_claude_needs_the_cli_and_a_waiting_source(client, waiting_source, monkeypatch):  # noqa: F811
    sid = waiting_source["id"]
    monkeypatch.setattr(claude_runner, "find_cli", lambda: None)
    r = client.post(f"/sources/{sid}/moments/claude")
    assert r.status_code == 400 and r.json()["code"] == "CLAUDE_CLI_NOT_FOUND"
    assert client.get(f"/sources/{sid}").json()["status"] == "waiting"  # nothing changed

    monkeypatch.setattr(claude_runner, "find_cli", lambda: "claude")
    client.post(f"/sources/{sid}/moments/find", json={"provider": "heuristic"})
    run_queue()
    assert client.post(f"/sources/{sid}/moments/claude").json()["code"] == "CLAUDE_NOT_WAITING"


def test_build_command_is_headless_and_limited(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "sonnet")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        cmd = claude_runner.build_command("claude", "src_1")
    finally:
        get_settings.cache_clear()
    assert cmd[:3] == ["claude", "-p", "/find-moments src_1"]
    assert "--permission-mode" in cmd and "Bash" not in cmd[cmd.index("--allowedTools") + 1]
    assert cmd[-2:] == ["--model", "sonnet"]


def test_child_never_gets_api_credentials():
    # so the run always uses the Claude Code login (subscription), never a billed API key
    assert {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"} <= set(claude_runner.DROPPED_ENV_VARS)
