import { useMemo, type CSSProperties } from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

import { FONT_FAMILY } from "../fonts";
import { activeWord, buildPages, displayText, pageAt, type CaptionPage, type CaptionStyle } from "../lib/captions";
import type { CompositionProps } from "../schema";

export const HIGHLIGHT = "#FFD400";
export const EMPHASIS = "#FF3D7F";
export const BOUNCE_COLORS = ["#FFD400", "#3DDC97", "#4FC3F7", "#FF6B6B", "#C77DFF"];

interface StyleProps {
  page: CaptionPage;
  t: number;
  frame: number; // composition frame
  fps: number;
  size: number; // font size in px
  language: string;
}

const outline = (size: number, ratio: number): CSSProperties => ({
  WebkitTextStroke: `${(size * ratio).toFixed(1)}px #000`,
  paintOrder: "stroke fill",
});

/** ALL CAPS, thick black outline, the spoken word in yellow, page pops in with a spring. */
function BoldPage({ page, t, frame, fps, size, language }: StyleProps) {
  const s = spring({ frame: frame - Math.round(page.start * fps), fps, config: { damping: 12, stiffness: 220, mass: 0.6 } });
  const active = activeWord(page, t);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", alignItems: "baseline", columnGap: size * 0.28, transform: `scale(${0.72 + 0.28 * s})` }}>
      {page.words.map((w, i) => (
        <span
          key={w.index}
          data-word={w.index}
          style={{
            color: i === active ? HIGHLIGHT : w.emphasis ? EMPHASIS : "#FFFFFF",
            fontWeight: 900,
            // emphasis is a real size (laid out, never overlaps); the spoken word only lifts
            fontSize: w.emphasis ? "1.12em" : undefined,
            transform: i === active ? `translateY(${-size * 0.05}px)` : undefined,
            textShadow: `0 ${size * 0.07}px 0 rgba(0,0,0,0.55)`,
            ...outline(size, 0.14),
          }}
        >
          {displayText(w.text, "bold", language)}
        </span>
      ))}
    </div>
  );
}

/** Each word bounces in with a spring when spoken, in rotating bright colors. */
function BouncePage({ page, frame, fps, size }: StyleProps) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", alignItems: "baseline", columnGap: size * 0.26 }}>
      {page.words.map((w) => {
        const local = frame - Math.round(w.start * fps);
        const s = local < 0 ? 0 : spring({ frame: local, fps, config: { damping: 8, stiffness: 190, mass: 0.5 } });
        return (
          <span
            key={w.index}
            data-word={w.index}
            style={{
              display: "inline-block",
              opacity: local < 0 ? 0 : 1,
              color: BOUNCE_COLORS[w.index % BOUNCE_COLORS.length],
              fontWeight: 800,
              fontSize: w.emphasis ? "1.18em" : undefined,
              transform: `translateY(${(1 - s) * size * 0.7}px) scale(${0.35 + 0.65 * s})`,
              ...outline(size, 0.09),
            }}
          >
            {w.text}
          </span>
        );
      })}
    </div>
  );
}

/** Minimal sentence case, soft fade-in, the spoken word slightly brighter. */
function CleanPage({ page, t, frame, fps, size }: StyleProps) {
  const fade = interpolate(frame - Math.round(page.start * fps), [0, Math.max(1, Math.round(0.25 * fps))], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const active = activeWord(page, t);
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        columnGap: size * 0.24,
        opacity: fade,
        transform: `translateY(${(1 - fade) * size * 0.25}px)`,
        // a tight dark halo keeps thin white text readable on bright footage
        textShadow: `0 0 ${size * 0.04}px rgba(0,0,0,0.9), 0 ${size * 0.03}px ${size * 0.2}px rgba(0,0,0,0.75)`,
      }}
    >
      {page.words.map((w, i) => (
        <span
          key={w.index}
          data-word={w.index}
          style={{ fontWeight: w.emphasis ? 800 : 600, color: w.emphasis ? HIGHLIGHT : "#FFFFFF", opacity: i === active ? 1 : 0.74 }}
        >
          {w.text}
        </span>
      ))}
    </div>
  );
}

const PAGES: Record<CaptionStyle, (p: StyleProps) => React.ReactElement> = { bold: BoldPage, bounce: BouncePage, clean: CleanPage };

/** Word-level karaoke captions from the EDL's caption words. */
export function Captions({ edl, language }: Pick<CompositionProps, "edl" | "language">) {
  const frame = useCurrentFrame();
  const { fps, width: W, height: H } = useVideoConfig();
  const c = edl.captions;
  const pages = useMemo(() => buildPages(c.words, edl.segments, c.words_per_page), [c.words, edl.segments, c.words_per_page]);
  const t = frame / fps;
  const page = c.enabled ? pageAt(pages, t) : null;
  if (!page) return null;
  const size = c.font_size * (W / 1080);
  const Page = PAGES[c.style];
  return (
    <div
      data-testid="caption-page"
      data-style={c.style}
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: c.position_y * H,
        transform: "translateY(-50%)",
        padding: `0 ${W * 0.07}px`,
        fontFamily: FONT_FAMILY,
        fontSize: size,
        lineHeight: 1.18,
        textAlign: "center",
      }}
    >
      <Page key={page.start} page={page} t={t} frame={frame} fps={fps} size={size} language={language} />
    </div>
  );
}
