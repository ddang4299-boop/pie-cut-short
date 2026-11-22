# ShortForge Local — Turn long videos into 9:16 shorts

## Goal
A **local, single-machine** app for a personal project.
Import a long video (file or URL) → AI proposes the best moments → **a human reviews** → the app builds 9:16 shorts with captions and optional ElevenLabs voiceover → **a human does the final edit** → export MP4 plus a bundle for finishing in CapCut.

## Principles
- Local-first. Network calls only for ElevenLabs and (optional) LLM APIs.
- AI only produces **JSON drafts**. A human can edit everything; nothing is rendered before human approval.
- Every pipeline step is an independent module: files + JSON in/out, re-runnable, cached by hash.
- API keys live only in `.env` (ship `.env.example`), never committed.
- Simple, readable code. Working first, pretty second.
- Auto-detect GPU (CUDA / Apple MPS); fall back to CPU.

## Languages (bilingual app)
- **Code, comments, commit messages, docs, logs: English.**
- **App UI: English and Vietnamese**, switchable at runtime.
  - Frontend uses `react-i18next`; all strings in `frontend/src/locales/en.json` and `vi.json`. **No hard-coded UI strings.**
  - Language switcher in the header and in Settings; default = browser language (vi → Vietnamese, otherwise English); choice persisted in settings.
  - Backend returns error **codes + params** (e.g. `{ "code": "DISK_SPACE_LOW", "params": {"needed_gb": 12} }`); the frontend translates them.
  - Timecodes, numbers and dates formatted per locale.
  - A test checks that `en.json` and `vi.json` have exactly the same keys.
- **Content language is separate from UI language:** transcripts, captions, titles and hooks follow the video's language (Vietnamese, English, or mixed). Moment `reason` text is written in the current UI language.
- Fonts must render Vietnamese diacritics correctly everywhere (UI and captions).

## Stack
- **Backend:** Python 3.11, FastAPI, SQLite (SQLModel), in-process background job queue (no Redis/Celery).
- **Media:** FFmpeg/ffprobe, yt-dlp, faster-whisper, PySceneDetect, MediaPipe (faces), ultralytics YOLO (people, optional), OpenCV, numpy.
- **Voice:** ElevenLabs (official `elevenlabs` Python SDK).
- **Moment selection LLM:** pluggable provider `claude_code | anthropic | openai | ollama | heuristic`.
  - **Default `claude_code`** (uses the Claude Pro subscription, no API cost): the backend writes `data/<source>/analysis_request.md`; the user runs the slash command `/find-moments <source_id>` in Claude Code; Claude reads the file and writes `moments.json`; the UI detects the new file and loads it.
  - `heuristic` is the offline fallback.
- **Frontend:** React + Vite + TypeScript + Tailwind, react-i18next, wavesurfer.js (waveforms), HTML5 video, @remotion/player (preview).
- **Render:** **Remotion** (default — single-pass render: word-level karaoke captions, spring animations, hook text, progress bar) + FFmpeg (cutting, audio mix, encoding). Encode with **NVENC** on NVIDIA GPUs, VideoToolbox on Mac, otherwise libx264. FFmpeg + ASS render path kept as fallback.

### Moment scoring rubric (5 dimensions)
Claude scores each segment 0–10 per dimension; total = weighted sum × 10 (0–100). Weights editable in Settings.
| Dimension | Weight | Meaning |
|---|---|---|
| hook_strength | 0.30 | Do the first 3 seconds hold attention? |
| coherence | 0.20 | Understandable on its own, no prior context needed |
| emotion | 0.20 | Emotion, surprise, humor, tension |
| value_density | 0.15 | Information/entertainment per second |
| payoff | 0.15 | Clear ending / punchline |
Claude must understand narrative arcs (setup → payoff), not match keywords. Each moment includes per-dimension scores + a reason.

## Folder structure
```
backend/
  app/main.py            # FastAPI routes
  app/models.py          # SQLModel
  app/jobs.py            # job queue + progress (SSE or polling)
  app/errors.py          # error codes (translated in the frontend)
  pipeline/preflight.py
  pipeline/ingest.py     # yt-dlp, ffprobe, 720p proxy
  pipeline/transcribe.py # faster-whisper | ElevenLabs Scribe
  pipeline/content_type.py
  pipeline/analyze.py    # scene cuts, loudness, silences
  pipeline/moments.py    # LLM providers
  pipeline/snap.py       # boundary snapping
  pipeline/prepare.py
  pipeline/reframe.py    # face/person tracking → crop keyframes
  pipeline/cursor_track.py
  pipeline/captions.py   # word groups → SRT/ASS
  pipeline/voice.py      # ElevenLabs TTS / isolator
  pipeline/render.py     # Remotion (default) / FFmpeg fallback
  pipeline/export_capcut.py
  tests/
frontend/
  src/locales/en.json, vi.json
  src/pages/Library.tsx
  src/pages/MomentsReview.tsx   # ★ checkpoint 1
  src/pages/ClipEditor.tsx      # ★ checkpoint 2
  src/pages/Export.tsx
  src/pages/Settings.tsx
remotion/                # composition that takes the EDL JSON: video, captions, hook, progress bar
.claude/commands/find-moments.md   # slash command: Claude reads the transcript and scores 8–12 moments
data/                    # media, cache, output (gitignored)
samples/                 # short 2–5 min sample videos for testing
scripts/e2e_sample.py
```

## Data model
- **Project**, **Source** (path, duration, fps, width, height, proxy_path, content_type, language)
- **Transcript**: `words[] {start, end, text, speaker?}`
- **Moment**: `source_id, start, end, scores{5}, score, title, hook, reason, category, status: proposed|approved|rejected`
- **Clip** = Edit Decision List (JSON) — the single source of truth for both preview and render:
```json
{
  "id": "clip_01",
  "source_id": "src_01",
  "segments": [{ "start": 12.3, "end": 41.0 }],
  "crop": { "mode": "auto|manual|split|cursor", "keyframes": [{ "t": 0.0, "x": 0.42, "y": 0.5, "zoom": 1.0 }],
            "split": { "top": [0,0,0.3,0.3], "bottom": [0.2,0,0.6,1] } },
  "captions": { "enabled": true, "style": "bold|bounce|clean", "position_y": 0.72, "words_per_page": 3, "words": [] },
  "hook_text": { "enabled": true, "text": "", "start": 0, "end": 3.5 },
  "progress_bar": { "enabled": true, "color": "#FFD400" },
  "voiceover": { "enabled": false, "script": "", "voice_id": "", "audio_path": null, "start": 0, "duck_db": -12 },
  "audio": { "normalize_lufs": -14, "isolate_voice": false },
  "platform": "youtube_shorts|tiktok|reels",
  "output": { "w": 1080, "h": 1920, "fps": 30 },
  "status": "draft|review|approved|rendered"
}
```

## Pipeline
0. **Preflight** — valid file (video + audio, readable codecs), free disk ≥ 3× source size, GPU (CUDA/MPS/CPU), ffmpeg present. Clear errors before anything heavy runs.
1. **Ingest** — file or URL (yt-dlp), ffprobe, 720p CFR proxy for smooth preview.
2. **Transcribe** — word timestamps (faster-whisper, GPU if available, `vad_filter=True`; optional ElevenLabs Scribe). Vietnamese + English, auto-detected, overridable.
3. **Detect content type** — `talking_head | screen_recording | podcast | gameplay_facecam` (face count, motion, speech ratio). Drives default reframe mode and LLM hints.
4. **Analyze** — scene cuts, loudness peaks, silences.
5. **Propose moments** — the LLM reads the **entire** transcript and scores 8–12 segments of 15–60s, each with scores, title, hook, reason.
6. ★ **Checkpoint 1 — human review** (approve/reject, adjust timecodes, add manual moments, choose caption style and target platform).
7. **Snap boundaries** — align cuts to word boundaries, sentence ends and the nearest silence (±0.5s), with 0.1–0.2s padding so words are never clipped.
8. **Prepare** — rough-cut extraction (stream copy with 2s padding both ends for speed; frame-accurate cuts happen at render), compute reframe keyframes by content type.
9. **Voice** — ElevenLabs (optional).
10. ★ **Checkpoint 2 — human edit** (crop, captions, hook, voice, audio).
11. **Render** — Remotion (default) or FFmpeg + ASS (fallback), 1080x1920, −14 LUFS, NVENC when available.
12. **Export** — platform preset + MP4 + SRT + stems + CapCut bundle.

### Caption styles
- **Bold** — ALL CAPS, thick black outline, the spoken word highlighted yellow, pop-in spring.
- **Bounce** — each word bounces in with a spring, rotating bright colors.
- **Clean** — minimal sentence case, soft fade-in.
All support Vietnamese diacritics and word-level karaoke highlighting from word timestamps.

### Reframe by content type
- talking_head → follow the face · podcast → follow the active speaker · gameplay_facecam → split layout
- **screen_recording → follow the mouse cursor**: detect the cursor via frame differencing (small changed regions between consecutive frames, ignoring large changes like scrolling), smooth, and pan the crop to follow.

### Platform presets
| Platform | Max length | Encoding | Notes |
|---|---|---|---|
| YouTube Shorts | 60s default (check current policy) | H.264 ~12 Mbps, AAC 192k | keep clear of right-side buttons |
| TikTok | 60–180s | H.264 ~10 Mbps | keep text out of the bottom 20% |
| Instagram Reels | 90s | H.264 ~8 Mbps, 30 fps | cover image from first frame |
Defaults only; editable in Settings.

## UI specification

### Conventions
- **Layout:** 64px icon-only left sidebar with tooltips (Library, Moments, Editor, Export, Settings). Header: current source name, language switcher (EN / VI), running-jobs badge (click → "Jobs" drawer with progress + logs).
- **Colors (dark):** background `#0B0B0F`, panels `#15151C`, borders `#26262F`, text `#EDEDED`, secondary text `#9A9AA5`, **one** accent `#FF3D7F`, success `#3DDC97`, warning `#FFB020`, error `#FF5A5A`. UI font Inter (with Vietnamese subset), timecodes JetBrains Mono.
- **Timecodes** shown as `mm:ss.ff` everywhere; every time field accepts typing or horizontal drag-to-scrub.
- **Every screen has 3 states:** empty (one-line guidance + primary action), loading (skeleton), error (plain-language message + "Retry" + "View log").
- **Autosave** with 500ms debounce, "Saving… / Saved" indicator in the header. **Undo/Redo** based on EDL history (100 steps) in Moments and Editor.
- Toasts for normal notices; confirmation modals for **credit-spending** or **destructive** actions.
- Minimum width 1280px, no mobile layout. Every button has a tooltip showing its shortcut.
- Vietnamese strings are often ~30% longer than English: layouts must not break in either language.

### 1. Library
- Large drop zone + URL field + "Choose file" button. After dropping, run **preflight immediately** and show green/yellow/red results; the pipeline starts only when the user clicks "Start".
- Source card grid: thumbnail, name, duration, resolution, content-type badge (click to change), **mini stepper** Preflight → Transcribe → Analyze → Moments (each: waiting / running % / done / error), approved / total moments.
- While waiting for Claude Code: card shows "Waiting for /find-moments" + **Copy command** button; updates automatically when `moments.json` appears.
- Card menu: Open Moments · Re-run from step… · Delete (ask whether to delete media files too).

### 2. Moments Review (checkpoint 1)
3 columns: **left 300px** moment list · **center** player + timeline · **right 380px** transcript.
- **List:** filters All / Proposed / Approved / Rejected; sort by score or time. Each card: total score in a colored ring (≥75 green, 50–74 yellow, <50 grey), title, hook, duration, 5 mini bars for the rubric (hover shows numbers), reason collapsed to 2 lines, ✓ / ✕ buttons.
- **Player:** 16:9 proxy; "Preview 9:16" toggle overlays the expected crop; plays only inside the selected moment, with a loop toggle.
- **Timeline:** top = whole-video minimap with moments colored by status (click to jump). Below = zoomed timeline around the selection (±10s): waveform, scene-cut ticks, grey silence regions, in/out handles that **snap to word boundaries** (hold Alt to disable). After a drag, run snap.py and show the snapped position.
- **Transcript:** grouped by sentence, current word highlighted; click a word → seek; sentences inside the selected moment get a tinted background; select text → floating **"Create moment"** button.
- **Bottom bar:** caption style + target platform for the selected moment; **"Approve & open Editor"** button.
- **Keys:** Space, J/K/L, ←/→ one frame, Shift+←/→ one second, I/O, A approve, R reject, ↑/↓ previous/next moment.

### 3. Clip Editor (checkpoint 2)
Layout: **left** 9:16 preview (Remotion Player) · **center top** 16:9 source video with a draggable crop box · **right** tabbed Inspector · **bottom** multi-track timeline. Playhead synced between both viewers.
- **Framing tab:** mode auto / manual / split / cursor; keyframe list (time, x, y, zoom; editable numbers); "+ Keyframe at playhead"; easing linear / ease-in-out; split: draw 2 source regions + top/bottom ratio; "Recompute automatically" (confirm first, it overwrites manual keyframes).
- **Captions tab:** 3 style cards Bold / Bounce / Clean with animated previews; font size; vertical position (drag directly on the preview); words per page (1–4); caption block list with inline text editing and timing drag; find & replace; manually mark words to emphasize.
- **Hook tab:** text, display time (default 0–3.5s), position, on/off.
- **Voice tab:** see Phase 5.
- **Audio tab:** original volume, target LUFS, Voice Isolator toggle, ducking.
- **Export tab:** platform, progress bar on/off + color, Render button.
- **Timeline tracks:** Video (S splits a segment, Delete removes a middle segment for jump-cuts), Captions, Hook, Voiceover, Audio (waveform), Crop (keyframe dots). Ctrl+scroll zooms.
- **Safe-zone overlay** (toggle): dims areas covered by TikTok/Shorts/Reels UI.
- "← Moments" returns to the exact previous position.

### 4. Export
- Queue: one row per clip — progress %, time remaining, renderer (Remotion/FFmpeg), encoder (NVENC/VideoToolbox/CPU), Cancel.
- Finished clips: thumbnail, Play, Download MP4, Open folder, **Export for CapCut**, **Import edited version from CapCut**.
- Multi-select approved clips → "Render all".

### 5. Settings
UI language (EN/VI) · LLM provider · whisper model (tiny → large-v3) · device · rubric weights · platform presets · output folder · ElevenLabs key status + default voice · cache size + clear button.

## ElevenLabs
- **TTS**: voiceover / spoken hook intro. Cache by hash(text + voice + settings) so repeats cost no credits.
- **Speech-to-Text (Scribe)**: optional replacement for faster-whisper.
- **Voice Isolator**: optional cleanup of the original audio.
- Always show the **character count / estimated credits** and ask for confirmation before any call.
- Read the latest ElevenLabs docs before coding (the API changes often). Key: `ELEVENLABS_API_KEY`.
- Voices should work for both Vietnamese and English scripts (use a multilingual model).

## CapCut
CapCut has no public API, so:
- **Level 1 (required) — CapCut bundle** folder: `clip_9x16_clean.mp4` (cropped, no captions), `clip_final.mp4`, `captions.srt`, `voiceover.mp3`, `original_audio.wav`, and `README.txt` with step-by-step import instructions **in both English and Vietnamese**.
- **Level 2 (experimental, isolated)**: generate a CapCut desktop draft project — only if a draft-generation library is still compatible with the installed CapCut version; otherwise skip. Must never block other work.
- **Round-trip**: allow importing an MP4 edited in CapCut back into Export as the final version.

## Repo map
| Step | Use now | Add later (Phase 8) |
|---|---|---|
| Sources | yt-dlp | Streamlink, TwitchDownloader (VOD + chat) |
| Preflight / media | FFmpeg, ffprobe | — |
| Transcribe | faster-whisper (+ built-in Silero VAD) | WhisperX (tighter word alignment), ElevenLabs Scribe |
| Scene cuts | PySceneDetect | TransNet V2 |
| Moment selection | Claude Code (`/find-moments`) | Hypeline (Twitch chat peaks) |
| Silence cutting | ffmpeg silencedetect | Auto-Editor |
| Reframe | MediaPipe, YOLO, OpenCV | ByteTrack (multi-person) |
| Audio | ffmpeg loudnorm, ElevenLabs Isolator | Pedalboard, RNNoise (offline, free) |
| Captions + render | Remotion (Bold/Bounce/Clean, karaoke, progress bar), SRT for CapCut | VideoLingo (translation) |
| Voice | ElevenLabs TTS | — |
| Editor | Custom React | Fork OpenCut if a full timeline is needed |
| Finishing | CapCut bundle | LosslessCut, Subtitle Edit (manual tools) |

## Acceptance criteria
Reference numbers for a mid-range GPU (e.g. RTX 3060); CPU being 3–5× slower is acceptable.
- **Performance:** transcribe a 30-min video ≤ 3 min (GPU); preview responds to EDL edits ≤ 200ms; render a 45s clip ≤ 90s.
- **Cut points:** zero in/out points inside a word (automated test against word timestamps).
- **Preview matches render:** ≤ 1 frame offset; compare 3 frames render vs preview, SSIM ≥ 0.95.
- **Audio:** −14 ± 1 LUFS, true peak ≤ −1 dBTP.
- **Captions:** ≤ 100ms offset from speech; Vietnamese diacritics 100% correct.
- **Crop:** max pan ~25% of frame width per second, no jitter when the subject is still.
- **i18n:** every UI screen fully translated in EN and VI; locale key parity test passes; no layout breaks in either language.
- **Resilience:** closing the app mid-job and reopening resumes from the unfinished step (state in SQLite), no leftover temp files.

## Edge cases to handle
| Case | Handling |
|---|---|
| No audio / music only | Explain, skip transcription, allow manual moments on the timeline |
| Source already vertical 9:16 | Skip reframe, keep framing |
| Very long video (> 2h) | Split the transcript into parts for /find-moments, process in chunks |
| Multiple people / no faces | Active speaker (podcast) → YOLO person → center crop |
| Variable frame rate | Normalize to CFR when creating the proxy |
| Corrupt file / unusual codec | Preflight reports it, pipeline does not continue |
| Disk full mid-job | Stop the job, report, delete temp files |
| Mixed Vietnamese and English speech | Auto-detect language, allow manual override |
| ElevenLabs out of quota / offline | App keeps working, voice features disabled with a notice |
| Remotion fails | Auto-switch to FFmpeg + ASS, log it |
| GPU encoder fails | Fall back to libx264 |

**Testing:** pytest for pure logic (snap, keyframe interpolation, scoring, captions, locale parity). `scripts/e2e_sample.py` runs the full pipeline on `samples/` and prints a report of the acceptance metrics above.

## Running the project
- Backend: `cd backend && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm run dev`
- Tests: `cd backend && pytest`

## Working rules for Claude
- Follow the phases in `PROMPTS.md`. Finish and verify one phase before starting the next.
- Each phase: pytest for pure logic, a manual run on `samples/`, and EN + VI strings for any new UI.
- Before editing many files, state a short plan.
- After each phase, update **Progress** below and commit.

## Progress
- [x] Phase 0 — Project skeleton + i18n
- [ ] Phase 0b — Reuse claude-shorts (if cloned)
- [ ] Phase 1 — Ingest + Transcribe + Library
- [ ] Phase 2 — Analyze + Moment proposals + Moments Review
- [ ] Phase 3 — Reframe + Clip Editor
- [ ] Phase 4 — Captions + Remotion
- [ ] Phase 5 — ElevenLabs
- [ ] Phase 6 — Render + Export + CapCut
- [ ] Phase 7 — Polish
- [ ] Phase 8 — (Optional) WhisperX, Auto-Editor, Pedalboard, VideoLingo, Twitch
