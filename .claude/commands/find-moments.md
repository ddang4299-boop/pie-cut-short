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
