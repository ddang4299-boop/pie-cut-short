"""Boundary snapping for moments (pure functions, no I/O).

Rules, in priority order, for each boundary within +/- WINDOW seconds of the requested time:
  1. a sentence boundary (start of a sentence for "in", end of a sentence for "out"),
  2. otherwise the nearest word boundary,
  3. then refine with a detected silence right next to that word (speech energy may begin a bit
     earlier / end a bit later than Whisper's word timestamps),
  4. finally add PAD seconds of air, without ever reaching into the neighbouring word
     (if the gap is shorter than 2 x PAD the cut goes to the middle of the gap).
A cut is never placed strictly inside a word.

Words are dicts {start, end, text}; silences are [start, end] pairs (seconds).
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from typing import Any

WINDOW = 0.5
PAD = 0.15
MIN_LENGTH = 1.0

SENTENCE_END = re.compile(r"[.!?…。！？]+[\"'”’»)\]]*$")

Word = dict[str, Any]


def is_sentence_end(text: str) -> bool:
    return bool(SENTENCE_END.search(text.strip()))


def sentence_start_indices(words: Sequence[Word]) -> set[int]:
    """Index of every word that starts a sentence (first word, or the word after a sentence end)."""
    out = {0} if words else set()
    for i in range(1, len(words)):
        if is_sentence_end(words[i - 1]["text"]):
            out.add(i)
    return out


def sentence_end_indices(words: Sequence[Word]) -> set[int]:
    out = {i for i, w in enumerate(words) if is_sentence_end(w["text"])}
    if words:
        out.add(len(words) - 1)
    return out


def word_containing(words: Sequence[Word], t: float) -> int | None:
    """Index of the word with start < t < end (strictly inside), else None."""
    starts = [w["start"] for w in words]
    i = bisect_right(starts, t) - 1
    if i >= 0 and words[i]["start"] < t < words[i]["end"]:
        return i
    return None


def _pick(candidates: list[int], preferred: set[int], key) -> int | None:  # noqa: ANN001
    pool = [i for i in candidates if i in preferred] or candidates
    return min(pool, key=key) if pool else None


def _air(edge: float, neighbour: float, pad: float, direction: int) -> float:
    """Move `edge` by `pad` towards `neighbour` (direction -1 = earlier, +1 = later), never past
    the middle of the gap when the gap is shorter than 2 x pad."""
    gap = (edge - neighbour) if direction < 0 else (neighbour - edge)
    if gap <= 0:
        return edge
    step = pad if gap >= 2 * pad else gap / 2
    return edge + direction * step


def snap_start(
    t: float,
    words: Sequence[Word],
    silences: Sequence[Sequence[float]] = (),
    *,
    window: float = WINDOW,
    pad: float = PAD,
    floor: float = 0.0,
    starts_of_sentences: set[int] | None = None,
) -> float:
    if not words:
        return max(floor, t)
    starts = [w["start"] for w in words]
    lo, hi = bisect_left(starts, t - window), bisect_right(starts, t + window)
    sentence = starts_of_sentences if starts_of_sentences is not None else sentence_start_indices(words)
    i = _pick(list(range(lo, hi)), sentence, key=lambda k: (abs(starts[k] - t), k))
    if i is None:
        i = word_containing(words, t)
        if i is None:
            return max(floor, t)  # in a long pause: cutting here clips nothing
    onset = starts[i]
    prev_end = words[i - 1]["end"] if i > 0 else floor
    for a, b in silences:  # silence that ends right before the word: speech energy starts at b
        if prev_end <= b < onset and onset - b <= window and a < b:
            onset = b
    return max(floor, _air(onset, prev_end, pad, -1))


def snap_end(
    t: float,
    words: Sequence[Word],
    silences: Sequence[Sequence[float]] = (),
    *,
    window: float = WINDOW,
    pad: float = PAD,
    ceiling: float | None = None,
    ends_of_sentences: set[int] | None = None,
) -> float:
    limit = ceiling if ceiling is not None else float("inf")
    if not words:
        return min(limit, t)
    ends = [w["end"] for w in words]
    # ends are not necessarily sorted if timestamps overlap; scan the window linearly from a bisect hint
    starts = [w["start"] for w in words]
    lo = max(0, bisect_left(starts, t - window - 5.0))
    hi = bisect_right(starts, t + window)
    candidates = [k for k in range(lo, hi) if abs(ends[k] - t) <= window]
    sentence = ends_of_sentences if ends_of_sentences is not None else sentence_end_indices(words)
    i = _pick(candidates, sentence, key=lambda k: (abs(ends[k] - t), -k))
    if i is None:
        i = word_containing(words, t)
        if i is None:
            return min(limit, t)
    offset = ends[i]
    next_start = words[i + 1]["start"] if i + 1 < len(words) else limit
    for a, b in silences:  # silence that starts right after the word: speech energy ends at a
        if offset < a <= next_start and a - offset <= window and b > a:
            offset = a
    return min(limit, _air(offset, next_start, pad, +1))


def _push_out_of_words(t: float, words: Sequence[Word], *, is_start: bool) -> float:
    """Last-resort guard: if t is inside a word, move it to that word's start (in) / end (out)."""
    i = word_containing(words, t)
    if i is None:
        return t
    return words[i]["start"] if is_start else words[i]["end"]


def snap_moment(
    start: float,
    end: float,
    words: Sequence[Word],
    silences: Sequence[Sequence[float]] = (),
    *,
    duration: float | None = None,
    window: float = WINDOW,
    pad: float = PAD,
) -> tuple[float, float]:
    """Snap both boundaries of a moment. Returns (start, end) rounded to milliseconds."""
    limit = duration if duration is not None else float("inf")
    start = max(0.0, min(start, limit))
    end = max(start, min(end, limit))
    s_idx = sentence_start_indices(words)
    e_idx = sentence_end_indices(words)
    s = snap_start(start, words, silences, window=window, pad=pad, starts_of_sentences=s_idx)
    e = snap_end(end, words, silences, window=window, pad=pad, ceiling=duration, ends_of_sentences=e_idx)
    if e - s < MIN_LENGTH:  # snapping collapsed a very short selection: keep the raw range, word-safe
        s = _push_out_of_words(start, words, is_start=True)
        e = _push_out_of_words(max(end, start + MIN_LENGTH), words, is_start=False)
        e = min(e, limit)
    s = _push_out_of_words(s, words, is_start=True)
    e = _push_out_of_words(e, words, is_start=False)
    e = min(round(e, 3), limit)  # clamp after rounding so the end never passes the duration
    return round(s, 3), e


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def dedupe(moments: list[dict[str, Any]], threshold: float = 0.6) -> list[dict[str, Any]]:
    """Drop moments that overlap a higher-scoring one by more than `threshold` of the shorter one."""
    kept: list[dict[str, Any]] = []
    for m in sorted(moments, key=lambda m: m.get("score", 0), reverse=True):
        span = (m["start"], m["end"])
        dup = False
        for k in kept:
            shorter = min(span[1] - span[0], k["end"] - k["start"]) or 1e-9
            if overlap(span, (k["start"], k["end"])) / shorter > threshold:
                dup = True
                break
        if not dup:
            kept.append(m)
    return kept
