import { describe, expect, it } from "vitest";

import type { Moment, Word } from "./api";
import {
  clampRange,
  countByStatus,
  filterMoments,
  groupSentences,
  historyPush,
  historyRedo,
  historyUndo,
  scoreTone,
  snapToWord,
  sortMoments,
  timeToX,
  wordRange,
  xToTime,
  zoomView,
  type HistoryState,
} from "./moments";

const word = (start: number, end: number, text: string): Word => ({ start, end, text });

function moment(id: number, start: number, score: number, status: Moment["status"] = "proposed"): Moment {
  return {
    id, source_id: "s", start, end: start + 20, duration: 20, scores: {}, score, title: "", hook: "", reason: "",
    category: "story", key_line: null, status, origin: "ai", provider: null, caption_style: "bold",
    platform: "tiktok", raw_start: null, raw_end: null, created_at: null, updated_at: null,
  };
}

describe("score tone", () => {
  it("uses the spec thresholds", () => {
    expect(scoreTone(75)).toBe("high");
    expect(scoreTone(74)).toBe("mid");
    expect(scoreTone(50)).toBe("mid");
    expect(scoreTone(49)).toBe("low");
  });
});

describe("filter / sort", () => {
  const list = [moment(1, 50, 60, "approved"), moment(2, 10, 90), moment(3, 30, 60, "rejected")];
  it("filters by status and counts", () => {
    expect(filterMoments(list, "approved").map((m) => m.id)).toEqual([1]);
    expect(filterMoments(list, "all")).toHaveLength(3);
    expect(countByStatus(list)).toEqual({ all: 3, proposed: 1, approved: 1, rejected: 1 });
  });
  it("sorts by score then time, or by time", () => {
    expect(sortMoments(list, "score").map((m) => m.id)).toEqual([2, 3, 1]);
    expect(sortMoments(list, "time").map((m) => m.id)).toEqual([2, 3, 1]);
    expect(sortMoments([moment(5, 9, 1), moment(4, 1, 2)], "time").map((m) => m.id)).toEqual([4, 5]);
  });
});

describe("sentences", () => {
  it("splits on punctuation and pauses", () => {
    const words = [word(0, 0.4, "Xin"), word(0.4, 0.8, "chào."), word(0.9, 1.2, "Hôm"), word(1.2, 1.5, "nay"), word(3, 3.4, "tiếp")];
    expect(groupSentences(words).map((s) => [s.first, s.last])).toEqual([[0, 1], [2, 3], [4, 4]]);
  });
});

describe("snap to word", () => {
  const words = [word(1, 1.5, "a"), word(1.6, 2.2, "b"), word(4, 4.4, "c")];
  it("moves a time inside a word to its boundary", () => {
    expect(snapToWord(1.7, words, "start")).toBe(1.6);
    expect(snapToWord(1.7, words, "end")).toBe(2.2);
  });
  it("snaps to a nearby boundary and leaves far times alone", () => {
    expect(snapToWord(2.5, words, "end")).toBe(2.2);
    expect(snapToWord(3.0, words, "start")).toBe(3.0);
    expect(snapToWord(3.0, [], "start")).toBe(3.0);
  });
});

describe("ranges", () => {
  it("finds overlapping words", () => {
    const words = [word(0, 1, "a"), word(1, 2, "b"), word(2, 3, "c")];
    expect(wordRange(words, 0.5, 1.5)).toEqual([0, 1]);
    expect(wordRange(words, 5, 6)).toBeNull();
  });
  it("clamps to the video and keeps a minimum length", () => {
    expect(clampRange(-1, 5, 10, "both")).toEqual({ start: 0, end: 5 });
    expect(clampRange(4.9, 5, 10, "start")).toEqual({ start: 4.5, end: 5 });
    expect(clampRange(9.8, 12, 10, "end")).toEqual({ start: 9.8, end: 10 });
  });
});

describe("history", () => {
  it("undoes and redoes in order and drops the redo stack on a new edit", () => {
    let h: HistoryState<number> = { past: [], future: [] };
    h = historyPush(h, 1);
    h = historyPush(h, 2);
    const u = historyUndo(h)!;
    expect(u.entry).toBe(2);
    h = u.state;
    const r = historyRedo(h)!;
    expect(r.entry).toBe(2);
    h = historyPush(historyUndo(r.state)!.state, 3);
    expect(h.future).toEqual([]);
    expect(h.past).toEqual([1, 3]);
    expect(historyUndo({ past: [], future: [] })).toBeNull();
  });
  it("keeps at most the limit", () => {
    let h: HistoryState<number> = { past: [], future: [] };
    for (let i = 0; i < 150; i++) h = historyPush(h, i);
    expect(h.past).toHaveLength(100);
    expect(h.past[0]).toBe(50);
  });
});

describe("timeline geometry", () => {
  it("zooms around a moment and maps time <-> x", () => {
    const view = zoomView(30, 50, 100);
    expect(view).toEqual({ start: 20, end: 60 });
    expect(zoomView(2, 5, 8)).toEqual({ start: 0, end: 8 });
    expect(timeToX(40, view, 400)).toBe(200);
    expect(xToTime(100, view, 400)).toBe(30);
  });
});
