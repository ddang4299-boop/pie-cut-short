"""Run /find-moments in Claude Code headless mode from the app (the "Run with Claude" button).

`claude -p "/find-moments <source_id>"` runs in the repo root with the user's own Claude Code login
(the Pro subscription, no API key). Claude may only read files and write files inside the repo; when it
exits, data/<source>/moments.json is imported exactly as if the user had run the command by hand.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

from app import errors, jobs, moments_service, tasks
from app.config import REPO_ROOT, get_settings
from app.models import SOURCE_PROCESSING, SOURCE_WAITING

JOB_KIND = "claude_moments"
LANE = "llm"
TIMEOUT_SECONDS = 30 * 60
ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep"
# Removed from the child's environment:
# - set by a parent Claude Code session; a nested `claude` refuses to start when they are present
# - API credentials: with them Claude Code bills the API instead of the user's subscription login
DROPPED_ENV_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
                    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def find_cli() -> str | None:
    configured = get_settings().claude_bin
    return shutil.which(configured) or shutil.which("claude")


def build_command(cli: str, source_id: str) -> list[str]:
    cmd = [cli, "-p", f"/find-moments {source_id}", "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED_TOOLS]
    model = get_settings().claude_model.strip()
    if model:
        cmd += ["--model", model]
    return cmd


def enqueue(source_id: str) -> None:
    src = tasks.load_source(source_id)
    if src.status != SOURCE_WAITING:
        raise errors.AppError(errors.CLAUDE_NOT_WAITING)
    if not find_cli():
        raise errors.AppError(errors.CLAUDE_CLI_NOT_FOUND)
    tasks.set_step(source_id, "moments", state="running", progress=0.05, stage="claude_running", error=None)
    tasks.update_source(source_id, status=SOURCE_PROCESSING, error=None)
    jobs.get_queue().enqueue(JOB_KIND, source_id=source_id, lane=LANE)


def _describe(event: dict[str, Any]) -> str | None:
    """One log line for a stream-json event (tool calls and the final result)."""
    if event.get("type") == "assistant":
        parts = []
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                inp = block.get("input") or {}
                target = inp.get("file_path") or inp.get("pattern") or ""
                parts.append(f"claude: {block.get('name')} {target}".rstrip())
        return "\n".join(parts) or None
    if event.get("type") == "result":
        cost = event.get("total_cost_usd")
        return f"claude: finished ({event.get('subtype')}, {event.get('num_turns')} turns" + (
            f", ~${cost:.2f} equivalent)" if isinstance(cost, (int, float)) else ")")
    return None


def _restore_waiting(source_id: str, issue: dict[str, Any] | None) -> None:
    tasks.set_step(source_id, "moments", state="waiting", progress=0.0, stage=None, error=issue)
    tasks.update_source(source_id, status=SOURCE_WAITING, error=None)


def handle(ctx: jobs.JobContext) -> None:
    """On any failure the source goes back to "waiting" (with the error shown), so the user can retry,
    copy the command, or use the offline finder."""
    assert ctx.source_id
    sid = ctx.source_id
    try:
        _run(ctx, sid)
    except jobs.JobCancelled:
        _restore_waiting(sid, None)
        raise
    except errors.AppError as exc:
        _restore_waiting(sid, exc.to_dict())
        raise
    except Exception as exc:
        _restore_waiting(sid, errors.issue(errors.CLAUDE_RUN_FAILED, detail=str(exc)[:300] or type(exc).__name__))
        raise


def _run(ctx: jobs.JobContext, sid: str) -> None:
    cli = find_cli()
    if not cli:
        raise errors.AppError(errors.CLAUDE_CLI_NOT_FOUND)
    started = time.time()
    cmd = build_command(cli, sid)
    ctx.log("run: claude " + " ".join(cmd[1:]))
    ctx.progress(0.05, step="moments", force=True)
    env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV_VARS}
    result: dict[str, Any] = {}
    tool_calls = 0
    try:
        proc = subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        raise errors.AppError(errors.CLAUDE_RUN_FAILED, detail=str(exc)[:300]) from exc
    ctx.attach_process(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.time() - started > TIMEOUT_SECONDS:
                raise errors.AppError(errors.CLAUDE_RUN_FAILED, detail="timed out after 30 minutes")
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                ctx.log(line[:500])
                continue
            if event.get("type") == "result":
                result = event
            text = _describe(event)
            if text:
                ctx.log(text)
                tool_calls += len(text.splitlines())
                ctx.progress(min(0.9, 0.05 + 0.04 * tool_calls), step="moments")
        proc.wait()
    finally:
        if proc.poll() is None:
            jobs._kill(proc)  # noqa: SLF001 - the same tree kill the queue uses on cancel
    ctx.check_cancelled()

    moments_file = moments_service.result_path(sid)
    if not (moments_file.exists() and moments_file.stat().st_mtime >= started - 1):
        detail = result.get("result") or f"exit code {proc.returncode}; moments.json was not written"
        raise errors.AppError(errors.CLAUDE_RUN_FAILED, detail=str(detail)[:300])
    ctx.progress(0.95, step="moments", force=True)
    # the source stays "processing" until here, so the watcher never imports a half-written file
    outcome = moments_service.check_result_file(sid, force=True)
    ctx.log(f"moments.json: {outcome}")
    if outcome != "imported":  # check_result_file stored MOMENTS_INVALID on the step
        error = tasks.load_source(sid).steps.get("moments", {}).get("error") or {}
        raise errors.AppError(errors.MOMENTS_INVALID, detail=(error.get("params") or {}).get("detail", outcome))
