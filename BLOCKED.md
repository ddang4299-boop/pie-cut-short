# Blocked — needs a human

| Since | Phase | What | Why I could not do it | What to do |
|---|---|---|---|---|
| 2026-11-23 09:20 | 3 | Verify "the sample clip follows the face smoothly" on a **real talking-head video**. | `samples/speech_en_sample.mp4` is a synthetic test pattern (no face), and downloading or recording people is outside what autopilot may do. Face/speaker/split tracking is verified on synthetic detections in `backend/tests/test_reframe.py` (speed cap, no jitter, speaker switches). | Put a 2–5 min talking-head clip in `samples/`, import it, approve a moment, open it in the Editor and watch the 9:16 preview (Framing tab shows the method and how often the face was found). |
