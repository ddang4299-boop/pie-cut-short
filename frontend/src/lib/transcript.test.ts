import { describe, expect, it } from "vitest";

import { wordIndexAt } from "./transcript";

const words = [
  { start: 0.5, end: 0.9 },
  { start: 1.0, end: 1.4 },
  { start: 3.0, end: 3.3 },
];

describe("wordIndexAt", () => {
  it("finds the word being spoken", () => {
    expect(wordIndexAt(words, 0.6)).toBe(0);
    expect(wordIndexAt(words, 1.0)).toBe(1);
    expect(wordIndexAt(words, 3.2)).toBe(2);
  });
  it("returns -1 before the first word and in long silences", () => {
    expect(wordIndexAt(words, 0.1)).toBe(-1);
    expect(wordIndexAt(words, 2.2)).toBe(-1);
    expect(wordIndexAt([], 1)).toBe(-1);
  });
  it("keeps the last word briefly after it ends", () => {
    expect(wordIndexAt(words, 1.5)).toBe(1);
  });
});
