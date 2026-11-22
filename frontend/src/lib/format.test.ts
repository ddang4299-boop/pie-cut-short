import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, formatPercent, formatTimecode, parseTimecode } from "./format";

describe("formatTimecode", () => {
  it("formats mm:ss.ff", () => {
    expect(formatTimecode(0)).toBe("00:00.00");
    expect(formatTimecode(75.456)).toBe("01:15.45");
    expect(formatTimecode(59.999)).toBe("00:59.99");
    expect(formatTimecode(12.3)).toBe("00:12.30");
  });
  it("adds hours when needed", () => {
    expect(formatTimecode(3675.5)).toBe("1:01:15.50");
  });
  it("handles bad input", () => {
    expect(formatTimecode(-3)).toBe("00:00.00");
    expect(formatTimecode(null)).toBe("--:--.--");
    expect(formatTimecode(Number.NaN)).toBe("--:--.--");
  });
});

describe("parseTimecode", () => {
  it("round-trips formatted values", () => {
    for (const value of [0, 12.3, 75.45, 3675.5]) {
      expect(parseTimecode(formatTimecode(value))).toBeCloseTo(value, 2);
    }
  });
  it("accepts plain seconds and decimal commas", () => {
    expect(parseTimecode("42")).toBe(42);
    expect(parseTimecode("1:02,5")).toBeCloseTo(62.5);
  });
  it("rejects garbage", () => {
    expect(parseTimecode("")).toBeNull();
    expect(parseTimecode("1::2")).toBeNull();
    expect(parseTimecode("abc")).toBeNull();
    expect(parseTimecode("1:2:3:4")).toBeNull();
  });
});

describe("other formatters", () => {
  it("formats durations", () => {
    expect(formatDuration(135.9)).toBe("2:16");
    expect(formatDuration(3723)).toBe("1:02:03");
  });
  it("formats bytes per locale", () => {
    expect(formatBytes(34_924_116, "en")).toBe("33.3 MB");
    expect(formatBytes(34_924_116, "vi")).toBe("33,3 MB");
    expect(formatBytes(512, "en")).toBe("512 B");
  });
  it("formats percent", () => {
    expect(formatPercent(0.423, "en")).toBe("42%");
    expect(formatPercent(1.5, "en")).toBe("100%");
  });
});
