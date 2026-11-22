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

## Tests
```
cd backend && ..\.venv\Scripts\python -m pytest     # preflight, error codes, /health, locale parity
cd frontend && npm test                             # locale parity, language detection
cd frontend && npm run build                        # type-check + production build
cd frontend && npm run smoke                        # browser smoke test (needs both servers + Edge or Chrome)
```

## Handy tools
```
.venv\Scripts\python scripts\make_sample.py                          # synthetic 3-min test video (no speech)
cd backend && ..\.venv\Scripts\python -m pipeline.preflight <video>  # preflight a file, prints JSON
```

## Layout
- `backend/` — FastAPI app (`app/`), pipeline steps (`pipeline/`), tests (`tests/`)
- `frontend/` — React + Vite + Tailwind UI; strings in `src/locales/en.json` and `vi.json`
- `data/` — media, cache and output (gitignored)
- `samples/` — test videos (gitignored)
- `DECISIONS.md` — choices made during unattended builds and how to change them
