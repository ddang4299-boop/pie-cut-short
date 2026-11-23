"""PieCut desktop app. Double-click this file (or the PieCut shortcut on the Desktop / Start Menu).

- Builds the UI (frontend/dist) when it is missing or older than the source.
- Starts the backend hidden (no console window); it serves the UI and the API on 127.0.0.1.
- Opens a native window (Microsoft Edge WebView2, via pywebview) with the full app.
- Closing the window stops the backend and everything it started (ffmpeg, node). Unfinished jobs
  resume from their last step the next time PieCut opens (state is in SQLite).

Logs: data/logs/desktop.log (launcher) and data/logs/desktop_backend.log (backend).
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
ICON = ROOT / "desktop" / "piecut.ico"
LOGO = FRONTEND / "public" / "piecut.svg"
DATA = ROOT / "data"
LOGS = DATA / "logs"
PID_FILE = DATA / "desktop_backend.json"
FIRST_PORT = 8765
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
APP_ID = "PieCut.Desktop"

LOGS.mkdir(parents=True, exist_ok=True)
_log_file = open(LOGS / "desktop.log", "a", encoding="utf-8")  # noqa: SIM115 - open for the app's lifetime


def log(msg: str) -> None:
    _log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    _log_file.flush()


# --------------------------------------------------------------------------
# backend process
# --------------------------------------------------------------------------
def health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if data.get("status") == "ok" else None
    except (OSError, ValueError):
        return None


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return str(pid) in out


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


class Backend:
    def __init__(self) -> None:
        self.pid: int | None = None
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None

    def adopt_leftover(self) -> bool:
        """A backend from a previous run that is still alive (e.g. the launcher crashed): reuse it, so
        two job queues never run on the same database."""
        try:
            info = json.loads(PID_FILE.read_text("utf-8"))
            pid, port = int(info["pid"]), int(info["port"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if pid_alive(pid) and health(port):
            self.pid, self.port = pid, port
            log(f"reusing backend pid={pid} port={port}")
            return True
        if pid_alive(pid):  # alive but not answering: stop it before starting a fresh one
            kill_tree(pid)
        PID_FILE.unlink(missing_ok=True)
        return False

    def start(self) -> None:
        port = next((p for p in range(FIRST_PORT, FIRST_PORT + 40) if port_free(p)), None)
        if port is None:
            raise RuntimeError("No free local port for the PieCut backend (8765-8804).")
        env = {**os.environ, "SERVE_FRONTEND": "1", "PYTHONIOENCODING": "utf-8"}
        out = open(LOGS / "desktop_backend.log", "a", encoding="utf-8")  # noqa: SIM115 - handed to the child
        out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start on port {port} =====\n")
        out.flush()
        self.proc = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT / "backend", env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        self.pid, self.port = self.proc.pid, port
        PID_FILE.write_text(json.dumps({"pid": self.pid, "port": port}), "utf-8")
        log(f"started backend pid={self.pid} port={port}")

    def wait_ready(self, timeout: float = 180.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("The backend stopped while starting. See data/logs/desktop_backend.log.")
            if self.port and health(self.port):
                return
            time.sleep(0.4)
        raise RuntimeError("The backend did not start in time. See data/logs/desktop_backend.log.")

    def stop(self) -> None:
        pid, self.pid = self.pid, None  # the closed event and the final cleanup both call this
        if pid:
            log(f"stopping backend pid={pid}")
            kill_tree(pid)
            PID_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# UI build
# --------------------------------------------------------------------------
def ui_is_stale() -> bool:
    index = DIST / "index.html"
    if not index.exists():
        return True
    built = index.stat().st_mtime
    sources = [FRONTEND / "index.html", FRONTEND / "package.json", FRONTEND / "vite.config.ts"]
    for folder in (FRONTEND / "src", FRONTEND / "public", ROOT / "remotion" / "src"):
        if folder.exists():
            sources += [p for p in folder.rglob("*") if p.is_file()]
    return any(p.exists() and p.stat().st_mtime > built for p in sources)


def build_ui() -> None:
    log("building the UI (npm run build)")
    r = subprocess.run("npm run build", cwd=FRONTEND, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    log(r.stdout[-3000:] + r.stderr[-3000:])
    if r.returncode != 0 or not (DIST / "index.html").exists():
        raise RuntimeError("Building the UI failed (npm run build). See data/logs/desktop.log.")


# --------------------------------------------------------------------------
# window
# --------------------------------------------------------------------------
def splash_html() -> str:
    logo = LOGO.read_text("utf-8") if LOGO.exists() else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;height:100%;background:#000;color:#fafafa;font:14px "Segoe UI",system-ui,sans-serif}}
      body{{display:grid;place-items:center;background:radial-gradient(60% 40% at 50% -8%,rgba(255,255,255,.07),transparent 70%),#000}}
      .box{{text-align:center}} svg{{width:96px;height:96px;filter:drop-shadow(0 12px 30px rgba(124,92,255,.35))}}
      h1{{font:400 44px Georgia,serif;margin:14px 0 6px;letter-spacing:-.02em}}
      #status{{color:#a1a1aa;min-height:20px}} #error{{color:#f0616d;max-width:560px;margin-top:10px;white-space:pre-wrap}}
      .bar{{width:180px;height:2px;margin:18px auto 0;background:rgba(255,255,255,.08);overflow:hidden;border-radius:2px}}
      .bar i{{display:block;width:40%;height:100%;background:#7c5cff;animation:m 1.2s ease-in-out infinite}}
      @keyframes m{{0%{{transform:translateX(-100%)}}100%{{transform:translateX(250%)}}}}
    </style></head><body><div class="box">{logo}<h1>PieCut</h1>
      <div id="status">Starting… · Đang khởi động…</div><div class="bar" id="bar"><i></i></div><div id="error"></div>
    </div></body></html>"""


def set_status(window, text: str) -> None:
    try:
        window.evaluate_js(f"document.getElementById('status').textContent = {json.dumps(text)}")
    except Exception:  # noqa: BLE001 - the window may already be closing
        pass


def show_error(window, text: str) -> None:
    try:
        window.evaluate_js(
            "document.getElementById('bar').style.display='none';"
            f"document.getElementById('status').textContent={json.dumps('PieCut could not start · PieCut không khởi động được')};"
            f"document.getElementById('error').textContent={json.dumps(text)}")
    except Exception:  # noqa: BLE001
        pass


def set_window_icon(window) -> None:
    """WinForms takes its icon from pythonw.exe; use the PieCut icon instead (window + taskbar)."""
    try:
        import clr  # pythonnet, installed with pywebview on Windows

        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")
        from System import Action  # type: ignore[import-not-found]
        from System.Drawing import Icon  # type: ignore[import-not-found]

        form = window.native
        icon = Icon(str(ICON))
        form.Invoke(Action(lambda: setattr(form, "Icon", icon)))
    except Exception as exc:  # noqa: BLE001 - cosmetic only
        log(f"window icon not set: {exc}")


def boot(window, backend: Backend) -> None:
    try:
        window.events.shown.wait(15)
        set_window_icon(window)
        if ui_is_stale():
            set_status(window, "Building the interface (first start can take a minute)… · Đang dựng giao diện…")
            build_ui()
        if not backend.adopt_leftover():
            set_status(window, "Starting the engine… · Đang khởi động máy chủ…")
            backend.start()
            backend.wait_ready()
        log(f"open http://127.0.0.1:{backend.port}/library")
        window.load_url(f"http://127.0.0.1:{backend.port}/library")
    except Exception as exc:  # noqa: BLE001 - shown in the window
        log("start failed:\n" + traceback.format_exc())
        show_error(window, str(exc))


def main() -> None:
    if not PYTHON.exists():
        ctypes.windll.user32.MessageBoxW(None, f"Python environment not found:\n{PYTHON}", "PieCut", 0x10)
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # own taskbar icon/group
    except (AttributeError, OSError):
        pass
    import webview  # pywebview

    backend = Backend()
    window = webview.create_window("PieCut", html=splash_html(), width=1440, height=900, min_size=(1280, 760),
                                   background_color="#000000", text_select=True)
    window.events.closed += backend.stop
    try:
        webview.start(boot, (window, backend), private_mode=False, storage_path=str(DATA / "webview"))
    finally:
        backend.stop()
        log("closed")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        log("fatal:\n" + traceback.format_exc())
        raise
