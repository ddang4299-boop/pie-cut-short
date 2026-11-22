/** Transcript helpers shared by the viewers. */

/** Index of the word being spoken at time `t` (last word whose start <= t), or -1. */
export function wordIndexAt(words: { start: number; end: number }[], t: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  // Between words (silence) nothing is highlighted.
  if (found >= 0 && t > words[found].end + 0.25) return -1;
  return found;
}
