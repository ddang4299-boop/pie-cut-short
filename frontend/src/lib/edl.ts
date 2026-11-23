/**
 * EDL helpers for the Clip Editor. The geometry, interpolation and time mapping mirror
 * backend/pipeline/keyframes.py exactly (shared test vectors in edl.vectors.json), so the
 * preview and the render agree.
 *
 * - Keyframe {t, x, y, zoom}: t = clip time (seconds on the concatenated segments); x, y = crop
 *   center normalized to the source frame; zoom 1 = the largest 9:16 rectangle inside the source.
 * - Regions are normalized [x, y, w, h].
 */

export type Easing = "linear" | "ease_in_out";
export type CropMode = "auto" | "manual" | "split" | "cursor";
export type CropFit = "fill" | "blur";
export interface Keyframe {
  t: number;
  x: number;
  y: number;
  zoom: number;
}
export interface Segment {
  start: number;
  end: number;
}
export type Region = [number, number, number, number];

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 4;
export const MIN_SEGMENT = 0.2;
export const CROP_MODES: CropMode[] = ["auto", "manual", "split", "cursor"];
export const EASINGS: Easing[] = ["linear", "ease_in_out"];

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const round = (v: number, digits: number) => Math.round(v * 10 ** digits) / 10 ** digits;

// --- geometry ---------------------------------------------------------------
export function cropSize(srcW: number, srcH: number, outAspect: number, zoom = 1): [number, number] {
  const z = clamp(zoom, MIN_ZOOM, MAX_ZOOM);
  let w: number;
  let h: number;
  if (srcW / srcH >= outAspect) {
    h = srcH;
    w = srcH * outAspect;
  } else {
    w = srcW;
    h = srcW / outAspect;
  }
  return [w / z, h / z];
}

/** Normalized crop rectangle [left, top, width, height] for a center + zoom, clamped to the frame. */
export function cropRect(x: number, y: number, zoom: number, srcW: number, srcH: number, outAspect: number): Region {
  const [w, h] = cropSize(srcW, srcH, outAspect, zoom);
  const cx = Math.min(Math.max(x * srcW, w / 2), srcW - w / 2);
  const cy = Math.min(Math.max(y * srcH, h / 2), srcH - h / 2);
  return [(cx - w / 2) / srcW, (cy - h / 2) / srcH, w / srcW, h / srcH];
}

export function clampCenter(x: number, y: number, zoom: number, srcW: number, srcH: number, outAspect: number): [number, number] {
  const [left, top, w, h] = cropRect(x, y, zoom, srcW, srcH, outAspect);
  return [left + w / 2, top + h / 2];
}

export function isVertical(srcW: number, srcH: number, outAspect: number, tolerance = 0.02): boolean {
  return srcW / srcH <= outAspect * (1 + tolerance);
}

// --- interpolation ----------------------------------------------------------
export function ease(u: number, easing: Easing = "linear"): number {
  const v = clamp(u, 0, 1);
  return easing === "ease_in_out" ? v * v * (3 - 2 * v) : v;
}

export function sortKeyframes(keyframes: readonly Keyframe[]): Keyframe[] {
  return [...keyframes].sort((a, b) => a.t - b.t);
}

/** [x, y, zoom] at clip time t. No keyframes = centered, zoom 1. */
export function interpolate(keyframes: readonly Keyframe[], t: number, easing: Easing = "linear"): [number, number, number] {
  if (keyframes.length === 0) return [0.5, 0.5, 1];
  const kfs = sortKeyframes(keyframes);
  const first = kfs[0];
  const last = kfs[kfs.length - 1];
  if (t <= first.t) return [first.x, first.y, first.zoom ?? 1];
  if (t >= last.t) return [last.x, last.y, last.zoom ?? 1];
  for (let i = 0; i < kfs.length - 1; i++) {
    const a = kfs[i];
    const b = kfs[i + 1];
    if (a.t <= t && t <= b.t) {
      const span = b.t - a.t;
      const u = ease(span > 0 ? (t - a.t) / span : 1, easing);
      const za = a.zoom ?? 1;
      const zb = b.zoom ?? 1;
      return [a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u, za + (zb - za) * u];
    }
  }
  return [last.x, last.y, last.zoom ?? 1];
}

// --- clip time <-> source time -----------------------------------------------
export function clipDuration(segments: readonly Segment[]): number {
  return segments.reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0);
}

export function toClipTime(segments: readonly Segment[], sourceT: number): number | null {
  let offset = 0;
  for (const s of segments) {
    if (s.start <= sourceT && sourceT <= s.end) return offset + sourceT - s.start;
    offset += s.end - s.start;
  }
  return null;
}

export function toSourceTime(segments: readonly Segment[], clipT: number): number {
  if (segments.length === 0) return clipT;
  let offset = 0;
  for (const s of segments) {
    const length = s.end - s.start;
    if (clipT <= offset + length) return s.start + Math.max(0, clipT - offset);
    offset += length;
  }
  return segments[segments.length - 1].end;
}

/** Clip-time offset of each segment's start. */
export function segmentOffsets(segments: readonly Segment[]): number[] {
  const out: number[] = [];
  let offset = 0;
  for (const s of segments) {
    out.push(offset);
    offset += s.end - s.start;
  }
  return out;
}

/**
 * Where playback should be for a source time: inside a segment -> unchanged; in a gap or before
 * the first segment -> the start of the next segment; after the last -> null (stop).
 */
export function playableTime(segments: readonly Segment[], sourceT: number, epsilon = 0.02): number | null {
  for (const s of segments) {
    if (sourceT < s.start - epsilon) return s.start;
    if (sourceT <= s.end - epsilon) return sourceT;
  }
  return null;
}

// --- edits --------------------------------------------------------------------
/** Insert or replace the keyframe at clip time t (within 1/60 s). */
export function upsertKeyframe(keyframes: readonly Keyframe[], kf: Keyframe, tolerance = 1 / 60): Keyframe[] {
  const next = keyframes.filter((k) => Math.abs(k.t - kf.t) > tolerance);
  next.push({ t: round(Math.max(0, kf.t), 3), x: round(clamp(kf.x, 0, 1), 4), y: round(clamp(kf.y, 0, 1), 4), zoom: round(clamp(kf.zoom, MIN_ZOOM, MAX_ZOOM), 3) });
  return sortKeyframes(next);
}

export function removeKeyframe(keyframes: readonly Keyframe[], index: number): Keyframe[] {
  const sorted = sortKeyframes(keyframes);
  if (sorted.length <= 1) return sorted; // a clip always keeps one keyframe
  return sorted.filter((_, i) => i !== index);
}

/** Split the segment under clip time t into two (no-op when too close to an edge). */
export function splitSegment(segments: readonly Segment[], clipT: number): Segment[] {
  const offsets = segmentOffsets(segments);
  for (let i = 0; i < segments.length; i++) {
    const s = segments[i];
    const local = clipT - offsets[i];
    if (local > MIN_SEGMENT && local < s.end - s.start - MIN_SEGMENT) {
      const cut = round(s.start + local, 3);
      return [...segments.slice(0, i), { start: s.start, end: cut }, { start: cut, end: s.end }, ...segments.slice(i + 1)];
    }
  }
  return [...segments];
}

/**
 * Remove a segment (jump cut). Keyframes inside it are dropped and later ones move earlier so
 * the framing stays attached to the same source moments. The last segment cannot be removed.
 */
export function removeSegment(segments: readonly Segment[], keyframes: readonly Keyframe[], index: number): { segments: Segment[]; keyframes: Keyframe[] } {
  if (segments.length <= 1 || index < 0 || index >= segments.length) return { segments: [...segments], keyframes: [...keyframes] };
  const offset = segmentOffsets(segments)[index];
  const length = segments[index].end - segments[index].start;
  const kept = keyframes
    .filter((k) => k.t < offset - 1e-6 || k.t > offset + length + 1e-6)
    .map((k) => (k.t > offset + length ? { ...k, t: round(k.t - length, 3) } : k));
  // keep the framing at the cut point
  if (kept.length === 0 || !kept.some((k) => Math.abs(k.t - offset) < 1e-3)) {
    const [x, y, zoom] = interpolate(keyframes, offset + length);
    kept.push({ t: round(offset, 3), x, y, zoom });
  }
  return { segments: segments.filter((_, i) => i !== index), keyframes: sortKeyframes(kept) };
}

/** Normalized region centered on (cx, cy), w wide, with pixel aspect `aspect`, kept inside the frame. */
export function fitRegion(cx: number, cy: number, w: number, aspect: number, srcW: number, srcH: number): Region {
  let pw = w * srcW;
  let ph = pw / aspect;
  if (ph > srcH) {
    ph = srcH;
    pw = srcH * aspect;
  }
  if (pw > srcW) {
    pw = srcW;
    ph = srcW / aspect;
  }
  const x = clamp(cx * srcW - pw / 2, 0, srcW - pw);
  const y = clamp(cy * srcH - ph / 2, 0, srcH - ph);
  return [round(x / srcW, 4), round(y / srcH, 4), round(pw / srcW, 4), round(ph / srcH, 4)];
}

/** Pixel aspect (w/h) of the top and bottom panels of a split layout. */
export function splitAspects(outW: number, outH: number, ratio: number): [number, number] {
  return [outW / (outH * ratio), outW / (outH * (1 - ratio))];
}

export function defaultSplit(srcW: number, srcH: number, outW: number, outH: number, ratio = 0.4): { top: Region; bottom: Region; ratio: number } {
  const [topAspect, bottomAspect] = splitAspects(outW, outH, ratio);
  return { top: fitRegion(0.15, 0.2, 0.3, topAspect, srcW, srcH), bottom: fitRegion(0.5, 0.5, 1, bottomAspect, srcW, srcH), ratio };
}

/** Re-fit both regions after the ratio changed (keeps their centers and widths where possible). */
export function refitSplit(split: { top: Region; bottom: Region }, ratio: number, srcW: number, srcH: number, outW: number, outH: number) {
  const [topAspect, bottomAspect] = splitAspects(outW, outH, ratio);
  const refit = (r: Region, aspect: number) => fitRegion(r[0] + r[2] / 2, r[1] + r[3] / 2, r[2], aspect, srcW, srcH);
  return { top: refit(split.top, topAspect), bottom: refit(split.bottom, bottomAspect), ratio };
}

// --- platform safe zones --------------------------------------------------------
/**
 * Areas of the 9:16 frame covered by each app's UI (normalized [x, y, w, h]), dimmed by the
 * safe-zone overlay. Approximate defaults; the apps change their layouts from time to time.
 */
export const SAFE_ZONES: Record<string, Region[]> = {
  youtube_shorts: [
    [0, 0, 1, 0.08], // top bar
    [0.84, 0.42, 0.16, 0.42], // like / comment / share buttons
    [0, 0.82, 1, 0.18], // title, channel, subscribe
  ],
  tiktok: [
    [0, 0, 1, 0.09],
    [0.85, 0.36, 0.15, 0.48],
    [0, 0.8, 1, 0.2], // keep text out of the bottom 20 %
  ],
  reels: [
    [0, 0, 1, 0.08],
    [0.85, 0.5, 0.15, 0.36],
    [0, 0.8, 1, 0.2],
  ],
};
