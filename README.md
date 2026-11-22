# ShortForge Local

Turn long videos into 9:16 shorts on your own machine: import → AI proposes moments → you review →
captions, reframing and optional ElevenLabs voiceover → you edit → export MP4 + a CapCut bundle.
UI in English and Vietnamese. The full spec is in [CLAUDE.md](CLAUDE.md); build phases in [PROMPTS.md](PROMPTS.md).

## Requirements
- Python 3.11+ (developed on 3.14)
- Node.js 20+ (developed on 24)
- FFmpeg + ffprobe on `PATH` (or set `FFMPEG_BIN` / `FFPROBE_BIN` in `.env`)
- Optional: NVIDIA GPU (CUDA) or Apple Silicon (MPS). Everything also runs on CPU, slower.

## Setup (Windows; use `.venv/bin/...` on macOS/Linux)
```
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
cd frontend && npm install
```

## Run
Two terminals:
```
cd backend && ..\.venv\Scripts\uvicorn app.main:app --reload     # http://127.0.0.1:8000
cd frontend && npm run dev                                        # http://localhost:5173
```
(With the venv activated, plain `uvicorn app.main:app --reload` works too.)
Open http://localhost:5173. The header shows a green "Backend online" pill when the backend is reachable.
The frontend calls the backend through the Vite proxy at `/api/*`.

## Using the Library (Phase 1)
1. Drop a video on the Library page, click **Choose file** (Ctrl+O), or paste a link (yt-dlp) or a full
   local path (used in place, not copied). A preflight check runs immediately (green / yellow / red).
2. Pick the spoken language (auto / Vietnamese / English / mixed), optionally add notes, press **Start**.
3. The card's stepper shows Preflight → Preview copy (720p CFR proxy) → Transcribe → Analyze → Moments.
   The **Jobs** drawer in the header has progress, logs and Cancel for every background job.
4. **View transcript** plays the proxy and highlights the current word; click a word to seek.

The first transcription downloads the Whisper model into `data/models` (large-v3 ≈ 3 GB).
Transcripts are cached by file hash + model + language, so re-runs are instant.
Closing the backend mid-job is safe: on restart, unfinished jobs are re-queued and temp files removed.

## Tests
```
cd backend && ..\.venv\Scripts\python -m pytest     # preflight, ingest, transcribe, jobs, API, locale parity
cd frontend && npm test                             # locale parity, language detection, formatting
cd frontend && npm run build                        # type-check + production build
cd frontend && npm run smoke                        # browser smoke test (needs both servers + Edge or Chrome)
                                                    # also uploads samples/speech_en_sample.mp4 and transcribes it
                                                    # (SMOKE_LIBRARY=0 to skip)
```

## Handy tools
```
.venv\Scripts\python scripts\make_sample.py                          # synthetic 3-min test video (no speech)
cd backend && ..\.venv\Scripts\python -m pipeline.preflight <video>  # preflight a file, prints JSON
powershell -File scripts\make_speech_sample.ps1                       # English speech sample (Windows TTS) for transcription tests
cd backend && ..\.venv\Scripts\python -m pipeline.ingest proxy <video> <out.mp4>   # make a 720p CFR proxy
cd backend && ..\.venv\Scripts\python -m pipeline.transcribe <video> --out t.json --model small --models-dir ../data/models   # transcribe (JSON lines)
```

## Layout
- `backend/` — FastAPI app (`app/`), pipeline steps (`pipeline/`), tests (`tests/`)
- `frontend/` — React + Vite + Tailwind UI; strings in `src/locales/en.json` and `vi.json`
- `data/` — media, cache and output (gitignored)
- `samples/` — test videos (gitignored)
- `DECISIONS.md` — choices made during unattended builds and how to change them
