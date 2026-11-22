# Prompts for Claude Code — one phase at a time

> **For Claude:** this file comes with `CLAUDE.md` (the main spec). Read both. Each time, do **only the requested phase**; when done, run the tests, check the phase's "Done when" plus "Acceptance criteria" in CLAUDE.md, update Progress, commit, then stop and report to the user.
> If the spec is contradictory or missing something important, ask before acting. If you see a better approach than the spec, propose it first; do not change it on your own.
> All code, comments, commits and docs in English. Every new UI string goes into **both** `en.json` and `vi.json` (natural Vietnamese, not machine-literal).
> **Appendix A** at the end is the full content of the `/find-moments` command — create exactly that file in Phase 2.
>
> **For the user:** put `CLAUDE.md` and `PROMPTS.md` in the project root. Start each phase in a new session (or `/clear`) and say: "Read CLAUDE.md and PROMPTS.md, do Phase X." For unattended runs use `run-overnight.ps1`.

---

## Autopilot mode
When a prompt starts with "AUTOPILOT MODE" (sent by `run-overnight.ps1` while the user is asleep), these rules **replace** every "ask the user" instruction in this file:
1. **Never ask.** Detect OS, GPU (`nvidia-smi`), python/node/ffmpeg versions with commands. When a choice is needed, take the simplest safe option and record it in `DECISIONS.md` (time · phase · decision · reason · how to change it).
2. **Scope:** work only inside the project folder. Python uses a project venv `.venv`; npm installs inside the project. Do not install system software, change machine settings, delete anything outside the project, `git push`, or call paid APIs.
3. **Git:** commit after every small step with messages like `phase X: ...` so later runs can resume. At the start of every run read `git log -15`, `git status`, `DECISIONS.md`, `BLOCKED.md` to see where things stand.
4. **Phase 0:** skip "ask me…"; detect instead.
5. **Phase 0b:** only if `vendor/claude-shorts` exists; otherwise write "skipped — not cloned" into `.autopilot/phase-0b.done`. Decide the reuse plan yourself and record it in `DECISIONS.md`.
6. **Test video:** use the .mp4 in `samples/`. If none exists, generate one with ffmpeg (`testsrc2` + `sine`, 3 minutes) plus a fake transcript fixture (JSON) to test the steps after transcription; state clearly in the report that real speech was not tested.
7. **`/find-moments` (Phase 2):** create the command file and backend flow. To test, act as Claude yourself: read the sample's `analysis_request.md` and write a valid `moments.json`, then verify the backend loads it.
8. **ElevenLabs (Phase 5):** without `ELEVENLABS_API_KEY`, still write all the code and test with mocks. **Never** call the real API, even if a key exists (no one is supervising credit spend); note "needs human testing" in the report.
9. **Blocked** by something only a human can do (install CUDA, sign in, provide a key): write it in `BLOCKED.md` (what, why, what the human must do), finish everything else, then still create the `.done` file with a first line `PARTIAL:` so the next phase can run.
10. **Only create `.autopilot/phase-X.done`** when: the code runs, `pytest` (and the frontend build for UI phases) passes, the locale parity test passes, and everything is committed. Content: a 3–5 line summary.
11. **`MORNING_REPORT.md`:** after each phase, append a section: what was done · how to try it · which tests passed/failed · important decisions · what the human should do after waking up. Write this report in **Vietnamese** (the user's language); everything else stays English.
12. Do not stop to wait for confirmation; do not skip tests to go faster.

---

## Phase 0 — Project skeleton + i18n
```
Read CLAUDE.md. It is the project spec.
Before coding, ask me: OS, NVIDIA GPU or Apple Silicon, whether ffmpeg/python/node are installed.
Then:
1. Create the backend/ and frontend/ structure from the spec.
2. FastAPI backend with GET /health returning the ffmpeg version and device (cuda/mps/cpu).
   Write pipeline/preflight.py: checks video+audio streams, readable codecs, free disk ≥ 3× source size, GPU; returns a list of error/warning codes with params.
   Write app/errors.py with the error code registry.
3. Frontend: Vite + React + TS + Tailwind, dark theme from the spec, 64px sidebar with 5 pages (Library, Moments, Editor, Export, Settings), header with EN/VI switcher.
   Set up react-i18next with src/locales/en.json and vi.json; default language from the browser; persist the choice. Add a test that both locale files have identical keys.
   Show /health status on the page (translated).
4. .env.example (ELEVENLABS_API_KEY, LLM_PROVIDER, ANTHROPIC_API_KEY, OPENAI_API_KEY, OLLAMA_MODEL, WHISPER_MODEL), .gitignore excluding data/, .env, .venv, node_modules.
5. requirements.txt, short README (English) on how to run. git init + commit.
Done when: both dev commands run, the browser shows a green backend status, and switching EN/VI changes all visible text.
```

## Phase 0b — Reuse claude-shorts (if cloned)
```
I cloned the claude-shorts repo into vendor/claude-shorts. Read its SKILL.md, CLAUDE.md, scripts/, remotion/ and references/.
Build a table: each step in my CLAUDE.md → which claude-shorts file can be reused (transcribe.py, detect_content.py, compute_reframe.py, snap_boundaries.py, preflight.sh, detect_gpu.sh, export.sh, remotion/, scoring-rubric.md, caption-styles.md, platform-specs.md).
Principle: reuse as much as possible; wrap those scripts as modules in backend/pipeline/ instead of rewriting; copy remotion/ as the renderer; use references/scoring-rubric.md for /find-moments.
What I add on top: the web UI (Library, Moments Review, Clip Editor, Export, Settings) in EN + VI, ElevenLabs, the CapCut bundle, SQLite state.
Check the repo's license before copying anything. Show me the table and plan before changing code.
```

## Phase 1 — Ingest + Transcribe + Library
```
Do Phase 1 per CLAUDE.md:
- POST /sources: accepts a file upload or a URL (yt-dlp). Run preflight, ffprobe metadata, create a 720p H.264 CFR proxy for preview.
- Background job queue with % progress and a status API (SSE or 1s polling); jobs resume after restart (state in SQLite).
- transcribe.py: faster-whisper, word_timestamps=True, vad_filter=True, language auto-detect (vi/en) with manual override, model chosen in .env. Save transcript JSON, cached by file hash.
- Library page per "UI specification → 1. Library": drop zone, URL field, preflight results, source cards with mini stepper, transcript viewer. All strings in EN + VI.
- pytest for ffprobe parsing, preflight and caching.
Done when: dropping a 5-min sample video produces a proxy + word-level transcript visible in the UI, in both languages.
```

## Phase 2 — Analysis + Moment proposals + Checkpoint 1
```
Do Phase 2:
- content_type.py: classify talking_head | screen_recording | podcast | gameplay_facecam (MediaPipe face count on ~30 frames, motion level, speech ratio). Show it in the UI, editable.
- analyze.py: PySceneDetect scene cuts, per-second loudness via ffmpeg (ebur128/astats), silencedetect.
- moments.py: LLMProvider interface: claude_code (default), anthropic, openai, ollama, heuristic.
  * claude_code: write data/<source>/analysis_request.md **exactly in the format of Appendix A.1** (including ui_language). Create .claude/commands/find-moments.md with **the content of Appendix A.2** (unchanged except paths if the structure differs). The backend watches data/<source>/moments.json, validates it against schema A.3 (pydantic), recomputes the total score from the dimension scores using the weights in Settings, runs snap.py on each moment, then loads it into the DB.
  * API providers: same prompt, pydantic validation, retry on malformed output.
  * heuristic: score by loudness peaks + speech density + exclamations.
- snap.py: snap in/out to word boundary → sentence end → nearest silence within ±0.5s, add 0.15s padding; drop duplicates. Runs after proposals and after every manual in/out drag.
- Moments Review page per "UI specification → 2. Moments Review", all strings in EN + VI, including a "Copy /find-moments <id>" button.
- pytest for snap.py.
Done when: running /find-moments on the sample shows 8–12 proposals in the UI; I can adjust in/out, approve 2, create 1 manually, reload, and everything persists.
```

## Phase 3 — Reframe + Clip Editor
```
Do Phase 3:
- When a Moment is approved → create a Clip (EDL JSON per CLAUDE.md).
- prepare.py: rough-cut with stream copy plus 2s padding on both ends (fast); frame-accurate cuts are left to render.
- Default reframe mode by content type: talking_head → face; podcast → active speaker (face with mouth movement); screen_recording → cursor; gameplay_facecam → split.
- reframe.py: sample at 5 fps, MediaPipe face detection (fallback YOLO person), pick the main subject, smooth with a One-Euro filter, reduce to few keyframes, cap pan speed to avoid jitter. Split mode: facecam on top + gameplay below with 2 user-drawn regions.
- cursor_track.py (screen recordings): frame differencing between consecutive frames (grayscale, threshold, morphology), keep cursor-sized changed regions, ignore large ones (scrolling, window switches); hold position when lost; One-Euro smoothing; gentle 1.2–1.5× zoom so text stays readable. Fallback: blurred-background layout with the screen centered.
- Clip Editor page per "UI specification → 3. Clip Editor" (this phase: Framing tab, basic Audio tab and the timeline; other tabs as empty placeholders). 9:16 preview temporarily via CSS transform on the proxy (Phase 4 replaces it with @remotion/player using the same keyframe interpolation function). Draggable crop box on the source; add/remove keyframes; undo/redo; autosave. EN + VI strings.
- pytest for keyframe interpolation and smoothing.
Done when: the sample clip follows the face smoothly, I manually edit one keyframe, and the preview updates instantly.
```

## Phase 4 — Captions + Remotion
```
Do Phase 4:
- Create a Remotion project in remotion/: one 1080x1920 composition that takes the EDL JSON as props (validated with Zod) and renders: the source video cropped by keyframes, captions, hook text (default 0–3.5s), and a bottom progress bar.
- 3 caption styles as React components:
  * Bold: ALL CAPS, thick black outline, spoken word highlighted yellow, pop-in spring.
  * Bounce: each word bounces in with spring(), rotating bright colors.
  * Clean: minimal sentence case, fade-in.
  All with word-level karaoke highlighting from word timestamps, and a font with full Vietnamese diacritics (e.g. Be Vietnam Pro / Montserrat). Note: ALL CAPS must keep Vietnamese diacritics correct (use locale-aware uppercasing).
- captions.py: group 1–4 words per page, export SRT (for CapCut) and ASS (for the FFmpeg fallback).
- Clip Editor: switch the preview to @remotion/player, updating live on EDL edits; Captions and Hook tabs per the UI spec; progress bar toggle. EN + VI strings.
Done when: editing a word in the UI updates both the preview and the SRT; all 3 styles work with Vietnamese and English text.
```

## Phase 5 — ElevenLabs
```
Do Phase 5. First read the latest ElevenLabs docs and use the official Python SDK.
- voice.py:
  * list_voices() for picking a voice (with preview playback).
  * tts(script, voice_id, settings) → mp3 using a multilingual model (Vietnamese + English), cached by hash.
  * (optional) isolate_voice(audio) to clean the original audio.
  * (optional) Scribe as a transcription provider alternative to faster-whisper.
- Clip Editor Voice tab: script box ("Suggest script" uses the LLM from transcript + hook, in the video's language), voice picker, stability/similarity/speed sliders, Generate, play, place on the timeline, volume, ducking of the original (default -12 dB). EN + VI strings.
- Always show character count + estimated credits and require confirmation before any API call. Clear translated errors for missing key or exhausted quota.
Done when: I generate a voiceover for a clip, hear it in the preview over the ducked original, and regenerating the same script costs no extra credits.
```

## Phase 6 — Render + Export + CapCut
```
Do Phase 6:
- render.py, Remotion by default: pre-mix audio with FFmpeg (voiceover + sidechaincompress ducking, loudnorm -14 LUFS), then render the Remotion composition in one pass with the EDL + mixed audio (bundle once, render many). Final encode: h264_nvenc on NVIDIA, h264_videotoolbox on Mac, otherwise libx264 CRF 20 preset veryfast; AAC 192k. Detect automatically and fall back gracefully when the GPU encoder fails.
- FFmpeg + ASS fallback path (when Remotion fails or the user chooses it): cut segments, crop by keyframes, scale 1080x1920, burn ASS, same audio chain.
- Compare 3 frames render vs preview to confirm the crop matches (SSIM ≥ 0.95).
- Platform presets (YouTube Shorts, TikTok, Instagram Reels) from the table in CLAUDE.md: max length, bitrate, caption safe zones; warn when a clip exceeds the limit.
- export_capcut.py — CapCut bundle: clip_9x16_clean.mp4 (cropped, no captions), clip_final.mp4, captions.srt, voiceover.mp3, original_audio.wav, README.txt with step-by-step import instructions in English and Vietnamese.
- (Experimental, isolated) Investigate whether a CapCut desktop draft project can be generated for the installed version; if not certain, only report findings, do not implement.
- Export page per "UI specification → 4. Export". EN + VI strings.
Done when: one full clip renders and plays correctly on a phone; the bundle imports into CapCut.
```

## Phase 7 — Polish
```
Do Phase 7:
- Batch approve and batch render.
- Per-channel presets (caption style, voice, hook, position).
- Friendly translated errors, per-job logs, "re-run this step" buttons.
- Settings page per the UI spec (UI language, LLM provider, whisper model, device, rubric weights, platform presets, output folder, ElevenLabs status, cache cleanup).
- Review every screen in both EN and VI for missing strings and layout breaks.
- scripts/e2e_sample.py reporting the acceptance metrics.
- Update README and Progress in CLAUDE.md.
```

## Phase 8 — (Optional) Extensions from the repo map
```
Read "Repo map" in CLAUDE.md. Ask me which items to add, then do them one at a time:
- WhisperX: provider with tighter word alignment.
- Auto-Editor: optional "remove silences" inside a clip.
- Pedalboard + RNNoise: offline audio chain (denoise, EQ, compressor, limiter) as an alternative to ElevenLabs Isolator.
- VideoLingo: translate captions into other languages (e.g. vi ↔ en), export multiple SRTs.
- Streamlink/TwitchDownloader + Hypeline: download VODs with chat, use chat peaks as a signal for /find-moments.
Each item is an on/off option in Settings and must not break the default flow.
```

---

## Appendix A — The `/find-moments` command

### A.1 Format of `data/<source_id>/analysis_request.md` (generated by the backend)
```markdown
# Analysis request: <source_id>
- file: <original file name>
- duration: 00:42:17.50 (2537.5s)
- language: vi | en | mixed
- ui_language: vi | en
- content_type: talking_head | screen_recording | podcast | gameplay_facecam
- target_platform: youtube_shorts | tiktok | reels   (default for the whole source)
- clip_length: min 15s, max 60s
- wanted: 8-12 moments
- user_notes: <notes the user entered in Library, may be empty>
- part: 1/1   (if a long video was split: 1/3, 2/3, ...)

## Audio peaks (top 30, seconds:relative loudness 0-1)
123.4:0.92, 456.7:0.88, ...

## Scene cuts (seconds)
12.0, 45.3, ...

## Silences ≥ 0.4s (start-end, seconds)
33.1-33.9, 78.2-79.0, ...

## Transcript (one sentence per line, times in seconds)
[12.34-15.80] First sentence.
[15.80-19.02] Second sentence.
...
```
Splitting rule: if the transcript exceeds ~25,000 words, split it into `analysis_request.part1.md`, `part2.md`… at sentence boundaries, each part overlapping the next by 60 seconds.

### A.2 Content of `.claude/commands/find-moments.md`
```markdown
---
description: Read the transcript and pick 8–12 moments worth turning into short videos
argument-hint: <source_id>
---

You are an experienced short-form video editor. Task: find segments of a long video that can **stand alone** as a compelling 9:16 short.

## Step 1 — Read the data
- Read **all** of `data/$ARGUMENTS/analysis_request.md`. If `analysis_request.partN.md` files exist, read each part in turn, write a temporary shortlist to `data/$ARGUMENTS/moments.partN.json`, then make the final selection across all shortlists.
- Do not guess the content from a few sentences. Do not skip the middle or end of the video.

## Step 2 — Understand the video
Write for yourself (no file needed) 3–5 lines: what the video is about, its threads/topics, where the peaks are.

## Step 3 — Find candidates (about 2× the number wanted)
Prefer:
- Strong opinions, controversial or contrarian statements
- Short stories with a setup, a turn and an ending
- Concrete tips, how-tos, immediately usable knowledge
- Surprising numbers or facts
- Funny moments, strong emotional reactions (cross-check Audio peaks)
- Reveals, turning points, plot twists
- Gameplay: peak plays + the player's reaction (often matching loudness peaks)
Understand the narrative arc (setup → payoff); **never** pick a segment just because a keyword appears.

## Step 4 — Score with the rubric (each dimension 0–10)
**hook_strength (×0.30)** — do the first 3 seconds hold attention?
- 9–10: the opening line itself creates curiosity / shock / debate, understandable with no context
- 5–6: good idea but a slow start or needs one lead-in sentence
- 0–3: opens with filler ("so", "and", "but", "like I said", Vietnamese "ừ", "thì", "và", "nhưng") or greetings

**coherence (×0.20)** — would a stranger fully understand this segment alone?
- 9–10: complete context, no references to anything outside the segment
- 5–6: requires a little guessing
- 0–3: uses "that thing", "as above", "he" without making clear who/what

**emotion (×0.20)** — clear emotion (humor, surprise, excitement, tension, poignancy)?
- 9–10: strong emotion, evident in words and loudness
- 5–6: mild emotion
- 0–3: flat, neutral

**value_density (×0.15)** — value per second
- 9–10: no filler, every sentence adds information or entertainment
- 5–6: some rambling
- 0–3: mostly lead-in and repetition

**payoff (×0.15)** — is there a clear ending / punchline?
- 9–10: ends on a punchline, conclusion, result, or memorable line
- 5–6: acceptable but somewhat open-ended
- 0–3: cut off before the idea is complete

score = round((hook×0.30 + coherence×0.20 + emotion×0.20 + value×0.15 + payoff×0.15) × 10)

## Step 5 — Reject
Drop a segment if it:
- Starts mid-sentence or with a connector word
- Ends before the idea is complete
- Depends on visuals the transcript cannot convey (except gameplay with loudness peaks)
- Overlaps > 30% of its duration with a higher-scoring segment
- Contains sensitive personal information or content likely to be removed by platforms
- Scores < 40

## Step 6 — Boundaries
- start = the start time of a sentence; end = the end time of a sentence, right after the payoff.
- Length within clip_length; if a great idea runs longer, pick its strongest part rather than extending.
- If the best line is in the middle, put it in `hook` to use as opening text (never reorder the video).
- The backend snaps precisely to silences; you only need correct sentence boundaries.

## Step 7 — Write the text
- `title`: ≤ 60 characters, **in the video's language**, accurately describing the content.
- `hook`: ≤ 8 words, **in the video's language**, curiosity-inducing but **never false**.
- `reason`: 1–2 sentences **in ui_language**, why this segment works as a short.

## Step 8 — Write the result
Write `data/$ARGUMENTS/moments.json` exactly per schema A.3. Before writing, self-check:
- Valid JSON; 0 ≤ start < end ≤ duration; lengths within limits
- Sorted by score descending; scores match the formula
- Count within "wanted" (fewer if the video lacks good segments — then explain in `notes`)

Finally print a table to the terminal: #, start–end, length, score, title.
```

### A.3 `moments.json` schema
```json
{
  "source_id": "src_01",
  "summary": "3–5 line summary of the video (in ui_language)",
  "notes": "",
  "moments": [
    {
      "start": 312.40,
      "end": 356.10,
      "title": "Sai lầm khiến kênh mất 50% view",
      "hook": "Đừng bao giờ làm điều này",
      "reason": "Opens with a strong warning, tells a concrete story and ends with a clear lesson.",
      "category": "opinion|story|tip|fact|funny|reaction|reveal|gameplay",
      "scores": { "hook_strength": 9, "coherence": 8, "emotion": 7, "value_density": 8, "payoff": 9 },
      "score": 83,
      "key_line": [318.20, 321.75]
    }
  ]
}
```
(Example: a Vietnamese video with ui_language = en.) `key_line` (optional) is the single strongest line, used to suggest the hook and place caption emphasis.

---

### Tips for the Pro plan
- `/find-moments` runs inside Claude Code and uses Pro usage: longer videos mean larger transcripts. For videos > 1 hour, split the transcript into parts.
- One phase per session; use `/compact` if a session gets long.
- When something breaks, paste the full error log and say which step — don't say "fix everything".
- To change the design, edit `CLAUDE.md` first, then ask Claude to follow it.
