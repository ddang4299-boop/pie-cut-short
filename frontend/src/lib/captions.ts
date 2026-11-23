/**
 * Caption helpers for the editor. Paging is shared with the Remotion composition
 * (remotion/src/lib/captions.ts, mirrored by backend/pipeline/captions.py); the editing
 * helpers below change the EDL's caption words (stored in source time).
 */
import { buildPages, type CaptionPage, type CaptionWord } from "@composition/lib/captions";
import { toSourceTime, type Segment } from "@composition/lib/edl";

export * from "@composition/lib/captions";

/** Replace the text of one page. Same word count: one-to-one (timings kept); otherwise the new
 *  words share the page's time evenly. Empty text removes the page's words. */
export function setPageText(words: readonly CaptionWord[], page: CaptionPage, text: string): CaptionWord[] {
  const tokens = text.split(/\s+/).filter(Boolean);
  const idx = page.words.map((w) => w.index);
  const first = idx[0];
  const last = idx[idx.length - 1];
  const originals = idx.map((i) => words[i]);
  let replacement: CaptionWord[];
  if (tokens.length === originals.length) {
    replacement = originals.map((w, i) => ({ ...w, text: tokens[i] }));
  } else if (tokens.length === 0) {
    replacement = [];
  } else {
    const start = originals[0].start;
    const end = originals[originals.length - 1].end;
    const step = (end - start) / tokens.length;
    replacement = tokens.map((tok, i) => ({
      start: round(start + i * step),
      end: round(i === tokens.length - 1 ? end : start + (i + 1) * step),
      text: tok,
      emphasis: false,
    }));
  }
  return [...words.slice(0, first), ...replacement, ...words.slice(last + 1)];
}

/** Move/stretch a page to [clipStart, clipEnd] (clip time): its words are remapped linearly. */
export function retimePage(
  words: readonly CaptionWord[],
  segments: readonly Segment[],
  page: CaptionPage,
  clipStart: number,
  clipEnd: number,
): CaptionWord[] {
  const idx = page.words.map((w) => w.index);
  const oldStart = words[idx[0]].start;
  const oldEnd = words[idx[idx.length - 1]].end;
  const newStart = toSourceTime(segments, clipStart);
  const newEnd = Math.max(newStart + 0.05, toSourceTime(segments, clipEnd));
  const scale = oldEnd > oldStart ? (newEnd - newStart) / (oldEnd - oldStart) : 1;
  const map = (t: number) => round(newStart + (t - oldStart) * scale);
  const out = [...words];
  for (const i of idx) out[i] = { ...words[i], start: Math.max(0, map(words[i].start)), end: Math.max(0, map(words[i].end)) };
  return out;
}

/** Case-insensitive find & replace inside caption words. */
export function findReplace(words: readonly CaptionWord[], find: string, replace: string): { words: CaptionWord[]; count: number } {
  if (!find) return { words: [...words], count: 0 };
  const re = new RegExp(find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "giu");
  let count = 0;
  const out = words.map((w) => {
    const matches = w.text.match(re);
    if (!matches) return w;
    count += matches.length;
    return { ...w, text: w.text.replace(re, replace) };
  });
  return { words: out, count };
}

export function countMatches(words: readonly CaptionWord[], find: string): number {
  return find ? findReplace(words, find, "").count : 0;
}

export function toggleEmphasis(words: readonly CaptionWord[], index: number): CaptionWord[] {
  return words.map((w, i) => (i === index ? { ...w, emphasis: !w.emphasis } : w));
}

export function pagesOf(words: readonly CaptionWord[], segments: readonly Segment[], wordsPerPage: number): CaptionPage[] {
  return buildPages(words, segments, wordsPerPage);
}

const round = (t: number) => Math.round(t * 1000) / 1000;
