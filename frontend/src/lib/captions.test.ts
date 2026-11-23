import { describe, expect, it } from "vitest";

import { activeWord, buildPages, displayText, findReplace, pageAt, retimePage, setPageText, toggleEmphasis, type CaptionWord } from "./captions";
import vectors from "./captions.vectors.json";

describe("caption paging (shared vectors with backend/pipeline/captions.py)", () => {
  vectors.cases.forEach((c, n) => {
    it(`case ${n}: ${c.segments.length} segment(s), ${c.wpp} word(s) per page`, () => {
      const pages = buildPages(vectors.words as CaptionWord[], c.segments, c.wpp);
      expect(pages.length).toBe(c.pages.length);
      pages.forEach((p, i) => {
        const e = c.pages[i];
        expect(p.start).toBeCloseTo(e.start, 9);
        expect(p.end).toBeCloseTo(e.end, 9);
        expect(p.show_end).toBeCloseTo(e.show_end, 9);
        expect(p.words.map((w) => [w.index, w.seg, w.text, w.emphasis])).toEqual(e.words.map((w) => [w.index, w.seg, w.text, w.emphasis]));
        p.words.forEach((w, j) => {
          expect(w.start).toBeCloseTo(e.words[j].start, 9);
          expect(w.end).toBeCloseTo(e.words[j].end, 9);
        });
      });
    });
  });
});

describe("caption helpers", () => {
  const words: CaptionWord[] = [
    { start: 1, end: 1.4, text: "xin" },
    { start: 1.4, end: 1.8, text: "chào" },
    { start: 1.8, end: 2.4, text: "bạn." },
    { start: 3, end: 3.5, text: "Đặc" },
    { start: 3.5, end: 4, text: "biệt" },
  ];
  const segs = [{ start: 0, end: 10 }];

  it("uppercases Vietnamese without losing diacritics", () => {
    expect(displayText("đặc biệt ữ ộ ế", "bold", "vi")).toBe("ĐẶC BIỆT Ữ Ộ Ế");
    expect(displayText("đặc biệt", "clean", "vi")).toBe("đặc biệt");
  });

  it("finds the page and the spoken word", () => {
    const pages = buildPages(words, segs, 3);
    const p = pageAt(pages, 1.5)!;
    expect(p.words.map((w) => w.text)).toEqual(["xin", "chào", "bạn."]);
    expect(activeWord(p, 1.5)).toBe(1);
    expect(pageAt(pages, 0.5)).toBeNull();
  });

  it("edits page text one-to-one or redistributes timing", () => {
    const page = buildPages(words, segs, 3)[0];
    const same = setPageText(words, page, "Xin chào các");
    expect(same.map((w) => w.text)).toEqual(["Xin", "chào", "các", "Đặc", "biệt"]);
    expect(same[2].start).toBe(1.8);
    const fewer = setPageText(words, page, "Chào nhé");
    expect(fewer.map((w) => w.text)).toEqual(["Chào", "nhé", "Đặc", "biệt"]);
    expect(fewer[0].start).toBe(1);
    expect(fewer[1].end).toBe(2.4);
    expect(setPageText(words, page, "  ").length).toBe(2);
  });

  it("retimes a page linearly", () => {
    const page = buildPages(words, segs, 3)[0];
    const out = retimePage(words, segs, page, 2, 4.8);
    expect(out[0].start).toBe(2);
    expect(out[2].end).toBeCloseTo(4.8, 6);
    expect(out[1].start).toBeCloseTo(2.8, 6);
    expect(out[3]).toEqual(words[3]);
  });

  it("find & replace and emphasis", () => {
    const res = findReplace(words, "ĐẶC", "rất");
    expect(res.count).toBe(1);
    expect(res.words[3].text).toBe("rất");
    expect(toggleEmphasis(words, 4)[4].emphasis).toBe(true);
  });
});
