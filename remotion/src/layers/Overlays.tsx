import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

import { FONT_FAMILY } from "../fonts";
import type { CompositionProps } from "../schema";

/** Hook text: white card that pops in at `start` and fades out before `end`. */
export function Hook({ edl }: Pick<CompositionProps, "edl">) {
  const frame = useCurrentFrame();
  const { fps, width: W, height: H } = useVideoConfig();
  const hook = edl.hook_text;
  const t = frame / fps;
  const text = hook.text.trim();
  if (!hook.enabled || !text || t < hook.start || t >= hook.end) return null;
  const s = spring({ frame: frame - Math.round(hook.start * fps), fps, config: { damping: 14, stiffness: 180, mass: 0.7 } });
  const out = interpolate(t, [hook.end - 0.25, hook.end], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const size = 58 * (W / 1080);
  return (
    <div
      data-testid="hook-text"
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: hook.position_y * H,
        display: "flex",
        justifyContent: "center",
        transform: `translateY(-50%) scale(${0.82 + 0.18 * s})`,
        opacity: Math.min(s * 1.4, 1) * out,
      }}
    >
      <div
        style={{
          maxWidth: W * 0.86,
          padding: `${size * 0.32}px ${size * 0.6}px`,
          borderRadius: size * 0.38,
          backgroundColor: "#FFFFFF",
          color: "#0B0B0F",
          fontFamily: FONT_FAMILY,
          fontWeight: 800,
          fontSize: size,
          lineHeight: 1.2,
          textAlign: "center",
          boxShadow: `0 ${size * 0.15}px ${size * 0.5}px rgba(0,0,0,0.35)`,
        }}
      >
        {text}
      </div>
    </div>
  );
}

/** Thin bar along the bottom edge showing playback progress. */
export function ProgressBar({ edl }: Pick<CompositionProps, "edl">) {
  const frame = useCurrentFrame();
  const { durationInFrames, width: W } = useVideoConfig();
  if (!edl.progress_bar.enabled) return null;
  const h = Math.max(4, Math.round(12 * (W / 1080)));
  const progress = Math.min(1, (frame + 1) / durationInFrames);
  return (
    <div data-testid="progress-bar" style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: h, backgroundColor: "rgba(255,255,255,0.22)" }}>
      <div style={{ width: `${progress * 100}%`, height: "100%", backgroundColor: edl.progress_bar.color }} />
    </div>
  );
}
