import { describe, expect, it } from "vitest";

import {
  clipDuration,
  cropRect,
  defaultSplit,
  fitRegion,
  interpolate,
  playableTime,
  refitSplit,
  removeKeyframe,
  removeSegment,
  splitAspects,
  splitSegment,
  toClipTime,
  toSourceTime,
  upsertKeyframe,
  type Easing,
} from "./edl";
import vectors from "./edl.vectors.json";

const close = (a: number[], b: number[], digits = 9) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], digits));

describe("shared vectors with backend/pipeline/keyframes.py", () => {
  it("interpolate", () => {
    for (const c of vectors.interpolate) close(interpolate(vectors.keyframes, c.t, c.easing as Easing), c.out);
  });
  it("cropRect", () => {
    for (const c of vectors.crop_rect) {
      const [x, y, z, w, h] = c.in;
      close(cropRect(x, y, z, w, h, 9 / 16), c.out);
    }
  });
  it("time mapping", () => {
    for (const c of vectors.to_clip) {
      const got = toClipTime(vectors.segments, c.t);
      if (c.out === null) expect(got).toBeNull();
      else expect(got).toBeCloseTo(c.out, 9);
    }
    for (const c of vectors.to_source) expect(toSourceTime(vectors.segments, c.t)).toBeCloseTo(c.out, 9);
  });
});

describe("edits", () => {
  it("upserts keyframes at the same time and clamps values", () => {
    let kfs = upsertKeyframe([{ t: 0, x: 0.5, y: 0.5, zoom: 1 }], { t: 2, x: 1.4, y: 0.5, zoom: 9 });
    expect(kfs).toEqual([{ t: 0, x: 0.5, y: 0.5, zoom: 1 }, { t: 2, x: 1, y: 0.5, zoom: 4 }]);
    kfs = upsertKeyframe(kfs, { t: 2.005, x: 0.3, y: 0.4, zoom: 1.2 });
    expect(kfs).toHaveLength(2);
    expect(kfs[1].x).toBe(0.3);
    expect(removeKeyframe(kfs, 0)).toEqual([kfs[1]]);
    expect(removeKeyframe([kfs[1]], 0)).toEqual([kfs[1]]); // always keeps one
  });

  it("splits and removes segments, shifting keyframes", () => {
    const segs = [{ start: 10, end: 20 }];
    const split = splitSegment(segs, 4);
    expect(split).toEqual([{ start: 10, end: 14 }, { start: 14, end: 20 }]);
    expect(splitSegment(segs, 0.1)).toEqual(segs); // too close to the edge
    const twice = splitSegment(split, 6); // [10,14] [14,16] [16,20]
    expect(twice).toHaveLength(3);
    const kfs = [{ t: 0, x: 0.2, y: 0.5, zoom: 1 }, { t: 5, x: 0.4, y: 0.5, zoom: 1 }, { t: 8, x: 0.8, y: 0.5, zoom: 1 }];
    const cut = removeSegment(twice, kfs, 1);
    expect(cut.segments).toEqual([{ start: 10, end: 14 }, { start: 16, end: 20 }]);
    expect(clipDuration(cut.segments)).toBe(8);
    // t=5 was inside the removed part; t=8 moves to 6; a keyframe holds the framing at the cut (t=4)
    expect(cut.keyframes.map((k) => k.t)).toEqual([0, 4, 6]);
    expect(cut.keyframes[1].x).toBeCloseTo(interpolate(kfs, 6)[0], 6);
    expect(removeSegment(segs, kfs, 0).segments).toEqual(segs); // last segment stays
  });

  it("skips gaps during playback", () => {
    const segs = [{ start: 10, end: 12 }, { start: 15, end: 16 }];
    expect(playableTime(segs, 5)).toBe(10);
    expect(playableTime(segs, 11)).toBe(11);
    expect(playableTime(segs, 12.5)).toBe(15);
    expect(playableTime(segs, 16.5)).toBeNull();
  });

  it("fits split regions to the panel aspects", () => {
    const [topA, bottomA] = splitAspects(1080, 1920, 0.4);
    const split = defaultSplit(1920, 1080, 1080, 1920);
    const aspect = (r: number[]) => (r[2] * 1920) / (r[3] * 1080);
    expect(aspect(split.top)).toBeCloseTo(topA, 1);
    expect(aspect(split.bottom)).toBeCloseTo(bottomA, 1);
    const refit = refitSplit(split, 0.5, 1920, 1080, 1080, 1920);
    expect(aspect(refit.top)).toBeCloseTo(splitAspects(1080, 1920, 0.5)[0], 1);
    const r = fitRegion(0.99, 0.99, 0.3, 1.5, 1920, 1080);
    expect(r[0] + r[2]).toBeLessThanOrEqual(1.0001);
    expect(r[1] + r[3]).toBeLessThanOrEqual(1.0001);
  });
});
