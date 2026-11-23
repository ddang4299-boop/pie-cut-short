import { describe, expect, it } from "vitest";

import { DEFAULT_MUSIC } from "../../lib/api";
import { musicAt } from "./ClipAudio";

// Mirrors backend tests/test_music.py: the preview must follow the render's music rules.
const m = { ...DEFAULT_MUSIC, enabled: true, track_id: "0123456789abcdef0123456789abcdef", start: 1, offset: 3, fade_in: 0.5, fade_out: 2 };

describe("musicAt", () => {
  it("is silent before the start and plays from the offset", () => {
    expect(musicAt(m, 30, 10, 0.5)).toBeNull();
    expect(musicAt(m, 30, 10, 2)).toEqual({ trackT: 4, fade: 1 });
  });

  it("wraps to 0 when looping and stops at the track end otherwise", () => {
    expect(musicAt(m, 5, 10, 4)?.trackT).toBeCloseTo(1); // 3 + 3 = 6 -> 6 mod 5
    expect(musicAt({ ...m, loop: false }, 5, 10, 2.5)?.trackT).toBeCloseTo(4.5);
    expect(musicAt({ ...m, loop: false }, 5, 10, 3.1)).toBeNull(); // 2 s left after the offset
  });

  it("fades in and out linearly, capped at half the length", () => {
    expect(musicAt(m, 30, 10, 1.25)?.fade).toBeCloseTo(0.5);
    expect(musicAt(m, 30, 10, 9)?.fade).toBeCloseTo(0.5); // 1 s before the end of a 2 s fade-out
    const short = { ...m, start: 9, fade_in: 5, fade_out: 5 }; // 1 s of music -> 0.5 s fades
    expect(musicAt(short, 30, 10, 9.25)?.fade).toBeCloseTo(0.5);
  });

  it("is off when disabled or without a track", () => {
    expect(musicAt({ ...m, enabled: false }, 30, 10, 2)).toBeNull();
    expect(musicAt({ ...m, track_id: null }, 30, 10, 2)).toBeNull();
  });
});
