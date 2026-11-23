/**
 * Caption paging, mirrored exactly from backend/pipeline/captions.py (shared vectors in
 * frontend/src/lib/captions.vectors.json). Caption words are stored in source time; they are
 * mapped to clip time through the segments (a word belongs to the segment holding its midpoint).
 */
import type { Segment } from "./edl";

export interface CaptionWord {
  start: number;
  end: number;
  text: string;
  emphasis?: boolean;
}
export interface PageWord {
  index: number; // position in the EDL's captions.words
  seg: number;
  start: number; // clip time
  end: number;
  text: string;
  emphasis: boolean;
}
export interface CaptionPage {
  start: number;
  end: number;
  show_end: number;
  words: PageWord[];
}
export type CaptionStyle = "bold" | "bounce" | "clean";

export const CAPTION_STYLES: CaptionStyle[] = ["bold", "bounce", "clean"];
export const SENTENCE_END = [".", "!", "?", "…", "。"];
export const MAX_GAP = 0.6;
export const HOLD = 0.4;

export function mapWords(words: readonly CaptionWord[], segments: readonly Segment[]): PageWord[] {
  const offsets: number[] = [];
  let acc = 0;
  for (const s of segments) {
    offsets.push(acc);
    acc += s.end - s.start;
  }
  const out: PageWord[] = [];
  words.forEach((w, i) => {
    const text = String(w.text ?? "").trim();
    if (!text) return;
    const mid = (w.start + w.end) / 2;
    for (let k = 0; k < segments.length; k++) {
      const seg = segments[k];
      if (seg.start <= mid && mid < seg.end) {
        out.push({
          index: i,
          seg: k,
          start: offsets[k] + Math.max(w.start, seg.start) - seg.start,
          end: offsets[k] + Math.min(w.end, seg.end) - seg.start,
          text,
          emphasis: Boolean(w.emphasis),
        });
        break;
      }
    }
  });
  return out;
}

export function buildPages(
  words: readonly CaptionWord[],
  segments: readonly Segment[],
  wordsPerPage = 3,
  maxGap = MAX_GAP,
  hold = HOLD,
): CaptionPage[] {
  const perPage = Math.max(1, Math.floor(wordsPerPage));
  const groups: PageWord[][] = [];
  let current: PageWord[] = [];
  for (const w of mapWords(words, segments)) {
    if (current.length) {
      const prev = current[current.length - 1];
      if (
        current.length >= perPage ||
        w.start - prev.end > maxGap ||
        prev.seg !== w.seg ||
        SENTENCE_END.some((p) => prev.text.endsWith(p))
      ) {
        groups.push(current);
        current = [];
      }
    }
    current.push(w);
  }
  if (current.length) groups.push(current);
  const duration = segments.reduce((sum, s) => sum + (s.end - s.start), 0);
  return groups.map((group, i) => {
    const start = group[0].start;
    const end = Math.max(start, group[group.length - 1].end);
    const nextStart = i + 1 < groups.length ? groups[i + 1][0].start : duration;
    const show_end = nextStart >= end ? Math.max(end, Math.min(nextStart, end + hold)) : Math.max(start, nextStart);
    return { start, end, show_end, words: group };
  });
}

export const pageText = (page: CaptionPage) => page.words.map((w) => w.text).join(" ");

/** The page on screen at clip time t (pages are sorted and do not overlap). */
export function pageAt(pages: readonly CaptionPage[], t: number): CaptionPage | null {
  for (const p of pages) if (p.start <= t && t < p.show_end) return p;
  return null;
}

/** Index (within the page) of the word being spoken: the last one that has started. */
export function activeWord(page: CaptionPage, t: number): number {
  let active = 0;
  page.words.forEach((w, i) => {
    if (w.start <= t) active = i;
  });
  return active;
}

/** Bold captions are ALL CAPS; locale-aware so Vietnamese diacritics stay correct. */
export function displayText(text: string, style: CaptionStyle, language = "en"): string {
  if (style !== "bold") return text;
  const locale = language === "vi" || language === "mixed" ? "vi" : "en";
  return text.toLocaleUpperCase(locale);
}
