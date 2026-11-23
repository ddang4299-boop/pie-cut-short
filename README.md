# PieCut

<img src="frontend/public/piecut.svg" width="72" alt="PieCut logo">

Turn long videos into 9:16 shorts on your own machine: import → AI proposes moments → you review →
captions, reframing and optional ElevenLabs voiceover → you edit → export MP4 + a CapCut bundle.
UI in English and Vietnamese. The full spec is in [CLAUDE.md](CLAUDE.md); build phases in [PROMPTS.md](PROMPTS.md).

## Requirements
- Python 3.11+ (developed on 3.14)
- Node.js 20+ (developed on 24)
- FFmpeg + ffprobe on `PATH` (or set `FFMPEG_BIN` / `FFPROBE_BIN` in `.env`)
- Microsoft Edge or Google Chrome (Remotion renders with it; the UI smoke test drives it)
- Optional: NVIDIA GPU (CUDA, and NVENC for encoding) or Apple Silicon (MPS). Everything also runs on CPU, slower.

## Setup (Windows; use `.venv/bin/...` on macOS/Linux)
```
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
cd frontend && npm install
cd ..\remotion && npm install
```
`.env` holds API keys and machine paths and is never committed. Every key is optional:
without ElevenLabs the voice features are disabled with a notice; without an LLM key the default
moment finder is Claude Code (your subscription) and `heuristic` works fully offline.

## Desktop app (Windows)
PieCut runs as a normal Windows program in its own window (Microsoft Edge WebView2, built into Windows 11),
with no browser and no terminals.

One-time setup, after the steps above:
```
powershell -ExecutionPolicy Bypass -File desktop\install_shortcuts.ps1
```
This adds a **PieCut** shortcut to the Desktop and the Start Menu (pin it to the taskbar if you like).
Opening it:
1. Builds the UI into `frontend/dist` the first time, and again whenever the frontend source changed (about 10 s).
2. Starts the backend hidden on `127.0.0.1:8765` (the next free port if that one is taken) and shows a
   splash screen until it answers.
3. Opens the full app. Language, layout and other per-user settings persist between runs.

Closing the window stops the backend and anything it started (renders, ffmpeg). Unfinished jobs resume from
their last step the next time PieCut opens. If something goes wrong, the splash screen shows the error and the logs
are in `data/logs/desktop.log` and `data/logs/desktop_backend.log`. You can also double-click `PieCut.pyw` directly.
Run the shortcut script again if the repo folder moves. For development, keep using the two-terminal setup below
(hot reload).

### Ứng dụng desktop (Windows) — tiếng Việt
PieCut chạy như một phần mềm Windows bình thường trong cửa sổ riêng (dùng Microsoft Edge WebView2 có sẵn trong
Windows 11), không cần mở trình duyệt hay terminal.

Cài một lần, sau các bước Setup ở trên:
```
powershell -ExecutionPolicy Bypass -File desktop\install_shortcuts.ps1
```
Lệnh này tạo lối tắt **PieCut** trên Desktop và trong Start Menu (có thể ghim vào taskbar). Khi mở:
1. Lần đầu (và mỗi khi mã nguồn giao diện thay đổi) app tự dựng giao diện vào `frontend/dist` (khoảng 10 giây).
2. Backend chạy ẩn ở `127.0.0.1:8765` (hoặc cổng trống kế tiếp); màn hình chờ hiện cho tới khi backend sẵn sàng.
3. Mở toàn bộ ứng dụng. Ngôn ngữ, bố cục và các cài đặt cá nhân được giữ giữa các lần mở.

Đóng cửa sổ sẽ dừng backend và mọi tác vụ nó đang chạy (render, ffmpeg). Tác vụ dang dở sẽ tiếp tục từ bước còn
thiếu ở lần mở sau. Nếu có lỗi, màn hình chờ hiển thị thông báo; log nằm ở `data/logs/desktop.log` và
`data/logs/desktop_backend.log`. Bạn cũng có thể nhấp đúp trực tiếp vào `PieCut.pyw`. Nếu di chuyển thư mục dự án,
hãy chạy lại script tạo lối tắt. Khi phát triển, vẫn dùng cách chạy hai terminal bên dưới (tự tải lại khi sửa code).

## Run (development)
Two terminals:
```
cd backend && ..\.venv\Scripts\uvicorn app.main:app --reload     # http://127.0.0.1:8000
cd frontend && npm run dev                                        # http://localhost:5173
```
Open http://localhost:5173. The header shows a green "Backend online" pill when the backend is reachable.
The frontend calls the backend through the Vite proxy at `/api/*`.

## Workflow
1. **Library** — drop a video, click **Choose file** (Ctrl+O), or paste a link (yt-dlp) or a full local
   path (used in place, not copied). Preflight runs at once (green / yellow / red). Pick the spoken
   language (auto / Vietnamese / English / mixed) and press **Start**. The card's stepper follows
   Preflight → Preview copy (720p CFR proxy) → Transcribe → Analyze → Moments. The card menu has
   **Re-run from step…** and Delete.
2. **Moments** — with the default `claude_code` provider the card says *Waiting for /find-moments*:
   click **Copy command**, run `/find-moments <source_id>` in Claude Code in this folder, and the
   moments appear by themselves. Review them (checkpoint 1): approve / reject (A / R), adjust in/out
   on the timeline (snaps to words; hold Alt to turn snapping off), create a moment from a transcript
   selection, pick the caption style and platform. Tick several cards to approve or reject them in
   one go, optionally applying a **channel preset**. Ctrl+Z / Ctrl+Y undo and redo.
3. **Editor** — checkpoint 2: framing (auto / manual / split / cursor, keyframes), captions (Bold /
   Bounce / Clean, words per page, inline text edits), hook text, voiceover, audio, safe-zone overlay,
   multi-track timeline (S splits, Delete removes a middle segment). The Export tab applies a channel
   preset and starts the render.
4. **Export** — render queue with progress, renderer (Remotion / FFmpeg) and encoder (NVENC /
   VideoToolbox / CPU); **Render selected** for many clips; per-clip log and Retry; download the MP4,
   **Export for CapCut** (bundle folder + zip with a bilingual README), and **Import edited version
   from CapCut** to bring a CapCut edit back as the final.

Everything autosaves. Closing the backend mid-job is safe: on restart, unfinished jobs resume from
the unfinished step and temp files are removed.

## Settings
UI language · moment LLM provider and rubric weights · clip length and number of moments · Whisper
model and compute device (override `.env` without a restart) · default renderer, platform and
caption style · output folder · per-platform presets (max length, bitrates, fps) · **channel presets**
(platform, caption look, hook, progress bar, voice — applied in batch approval or in the editor) ·
ElevenLabs status and default voice · storage: cache sizes and a Clear button. Paid voice files,
Whisper models and rendered clips are never cleared.

## Costs and privacy
- Network calls happen only for yt-dlp downloads, ElevenLabs and the optional LLM APIs.
- Every ElevenLabs call shows the character count / estimated credits and asks first; results are
  cached by text + voice + settings, so repeats cost nothing. `ELEVENLABS_MOCK=1` uses local test
  tones (no network, no credits) for development.

## Tests
```
cd backend && ..\.venv\Scripts\python -m pytest     # pure logic + API (snap, keyframes, captions, render, locale parity…)
cd frontend && npm test                             # vitest: locale parity, captions vectors, channel presets…
cd frontend && npm run build                        # type-check + production build
cd frontend && npm run smoke                        # browser smoke test in EN + VI (needs both servers + Edge or Chrome)
                                                    # also uploads samples/speech_en_sample.mp4 (SMOKE_LIBRARY=0 to skip)
.venv\Scripts\python scripts\e2e_sample.py          # full pipeline on samples/ + acceptance report (see below)
```

### End-to-end report
`scripts/e2e_sample.py` runs every video in `samples/` through the whole pipeline in a throw-away
data folder (`data/e2e/run_<time>/`; `--keep` keeps it), using the offline `heuristic` moment finder
and a mocked ElevenLabs, so it costs nothing. It approves the best `--clips N` moments (default 1),
renders them, and prints the acceptance metrics: transcription speed (projected to a 30-min video),
cut points inside words, render time, loudness and true peak, preview-vs-render SSIM, caption timing
(data and an audio-onset estimate), Vietnamese diacritics coverage, crop pan speed and jitter, locale
parity and leftover temp files. The JSON report is saved as `data/e2e/report_<time>.json`; the exit
code is 1 when a metric fails. Options: `--renderer remotion|ffmpeg`, `--whisper-model`, `--device`, `--only NAME`.

## Handy tools
```
.venv\Scripts\python scripts\make_sample.py                          # synthetic 3-min test video (no speech)
powershell -File scripts\make_speech_sample.ps1                       # English speech sample (Windows TTS)
cd backend && ..\.venv\Scripts\python -m pipeline.preflight <video>  # preflight a file, prints JSON
cd backend && ..\.venv\Scripts\python -m pipeline.ingest proxy <video> <out.mp4>   # make a 720p CFR proxy
cd backend && ..\.venv\Scripts\python -m pipeline.transcribe <video> --out t.json --model small --models-dir ../data/models
cd remotion && npm run studio                                         # Remotion Studio for the caption composition
```

## Layout
- `backend/` — FastAPI app (`app/`), pipeline steps (`pipeline/`), tests (`tests/`), caption fonts (`assets/fonts/`)
- `frontend/` — React + Vite + Tailwind UI; strings in `src/locales/en.json` and `vi.json`
- `remotion/` — the composition that renders an EDL (video, karaoke captions, hook, progress bar)
- `.claude/commands/find-moments.md` — the `/find-moments` slash command
- `data/` — media, cache and output (gitignored) · `samples/` — test videos (gitignored)
- `DECISIONS.md` — choices made during unattended builds and how to change them ·
  `BLOCKED.md` — checks that need a human
