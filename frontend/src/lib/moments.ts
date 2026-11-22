/** Pure helpers for Moments Review (unit-tested in moments.test.ts). */

import type { Moment, MomentStatus, Word } from "./api";

export type MomentFilter = "all" | MomentStatus;
export type MomentSort = "score" | "time";

/** Ring color bucket for a 0–100 score: ≥75 high (green), 50–74 mid (yellow), <50 low (grey). */
export function scoreTone(score: number): "high" | "mid" | "low" {
  if (score >= 75) return "high";
  if (score >= 50) return "mid";
  return "low";
}

export function filterMoments(moments: Moment[], filter: MomentFilter): Moment[] {
  return filter === "all" ? moments : moments.filter((m) => m.status === filter);
}

export function sortMoments(moments: Moment[], sort: MomentSort): Moment[] {
  const copy = [...moments];
  if (sort === "time") copy.sort((a, b) => a.start - b.start || a.id - b.id);
  else copy.sort((a, b) => b.score - a.score || a.start - b.start);
  return copy;
}

export function countByStatus(moments: Moment[]): Record<MomentFilter, number> {
  const out: Record<MomentFilter, number> = { all: moments.length, proposed: 0, approved: 0, rejected: 0 };
  for (const m of moments) out[m.status] += 1;
  return out;
}

const SENTENCE_END = /[.!?…。！？]["'”’)\]]*$/;

export interface Sentence {
  start: number;
  end: number;
  first: number; // word index
  last: number; // word index (inclusive)
}

/** Group words into sentences: punctuation, a pause ≥ maxGap, or a long run without punctuation. */
export function groupSentences(words: Word[], maxGap = 1.0, softLen = 15, hardLen = 25): Sentence[] {
  const out: Sentence[] = [];
  let first = 0;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const next = words[i + 1];
    const gap = next ? next.start - w.end : 0;
    const length = w.end - words[first].start;
    const text = w.text.trim();
    const boundary =
      !next ||
      SENTENCE_END.test(text) ||
      gap >= maxGap ||
      (length >= softLen && (/[,;:—-]$/.test(text) || gap >= 0.25)) ||
      length >= hardLen;
    if (boundary) {
      out.push({ start: words[first].start, end: w.end, first, last: i });
      first = i + 1;
    }
  }
  return out;
}

/**
 * Snap a handle to the nearest word boundary (word starts for "start", word ends for "end").
 * A time inside a word always moves to that word's boundary; otherwise it only snaps when a
 * boundary is within `maxDist` seconds. The server (snap.py) refines this after the drag.
 */
export function snapToWord(t: number, words: Word[], edge: "start" | "end", maxDist = 0.5): number {
  if (!words.length) return t;
  let best = t;
  let bestDist = Infinity;
  for (const w of words) {
    if (t > w.start && t < w.end) {
      // inside a word: include the whole word (its start for in-points, its end for out-points)
      return edge === "start" ? w.start : w.end;
    }
    const b = edge === "start" ? w.start : w.end;
    const d = Math.abs(b - t);
    if (d < bestDist) {
      bestDist = d;
      best = b;
    }
  }
  return bestDist <= maxDist ? best : t;
}

/** Indices of words overlapping [start, end]. */
export function wordRange(words: Word[], start: number, end: number): [number, number] | null {
  let a = -1;
  let b = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].end > start && words[i].start < end) {
      if (a === -1) a = i;
      b = i;
    }
  }
  return a === -1 ? null : [a, b];
}

export function round3(x: number): number {
  return Math.round(x * 1000) / 1000;
}

/** Keep a moment inside [0, duration] and at least minLen long; `moving` says which edge moved. */
export function clampRange(start: number, end: number, duration: number, moving: "start" | "end" | "both", minLen = 0.5) {
  const limit = duration > 0 ? duration : Infinity;
  let s = Math.max(0, Math.min(start, limit));
  let e = Math.max(0, Math.min(end, limit));
  if (e - s < minLen) {
    if (moving === "start") s = Math.max(0, e - minLen);
    else e = Math.min(limit, s + minLen);
  }
  return { start: round3(s), end: round3(e) };
}

// ---------------------------------------------------------------------------
// Undo / redo
// ---------------------------------------------------------------------------
export interface HistoryState<T> {
  past: T[];
  future: T[];
}

export const HISTORY_LIMIT = 100;

export function historyPush<T>(h: HistoryState<T>, entry: T, limit = HISTORY_LIMIT): HistoryState<T> {
  const past = [...h.past, entry];
  if (past.length > limit) past.splice(0, past.length - limit);
  return { past, future: [] };
}

/** Returns the entry to undo and the new state, or null when there is nothing to undo. */
export function historyUndo<T>(h: HistoryState<T>): { entry: T; state: HistoryState<T> } | null {
  if (!h.past.length) return null;
  const entry = h.past[h.past.length - 1];
  return { entry, state: { past: h.past.slice(0, -1), future: [entry, ...h.future] } };
}

export function historyRedo<T>(h: HistoryState<T>): { entry: T; state: HistoryState<T> } | null {
  if (!h.future.length) return null;
  const [entry, ...rest] = h.future;
  return { entry, state: { past: [...h.past, entry], future: rest } };
}

// ---------------------------------------------------------------------------
// Timeline geometry
// ---------------------------------------------------------------------------
export interface View {
  start: number;
  end: number;
}

/** The zoomed timeline window: the moment ±pad seconds, clamped to the video. */
export function zoomView(start: number, end: number, duration: number, pad = 10): View {
  const lo = Math.max(0, start - pad);
  const hi = duration > 0 ? Math.min(duration, end + pad) : end + pad;
  return { start: lo, end: Math.max(hi, lo + 1) };
}

export function timeToX(t: number, view: View, width: number): number {
  return ((t - view.start) / (view.end - view.start)) * width;
}

export function xToTime(x: number, view: View, width: number): number {
  return view.start + (x / Math.max(1, width)) * (view.end - view.start);
}

/** Frame duration for ←/→ stepping. */
export function frameStep(fps: number | null | undefined): number {
  return 1 / (fps && fps > 1 ? fps : 30);
}
