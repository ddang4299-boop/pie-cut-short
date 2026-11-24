import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Analysis, Moment, Waveform, Word } from "../../lib/api";
import { formatTimecode } from "../../lib/format";
import { clampRange, snapToWord, timeToX, xToTime, zoomView, type View } from "../../lib/moments";

const STATUS_FILL: Record<Moment["status"], string> = {
  proposed: "bg-accent/60",
  approved: "bg-success/80",
  rejected: "bg-white/15",
};

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Whole-video strip: every moment colored by status; click to jump (and select). */
export const Minimap = memo(function Minimap({
  duration,
  moments,
  selectedId,
  time,
  onSeek,
  onSelect,
}: {
  duration: number;
  moments: Moment[];
  selectedId: number | null;
  time: number;
  onSeek: (t: number) => void;
  onSelect: (id: number) => void;
}) {
  const { t } = useTranslation();
  const [ref, width] = useWidth<HTMLDivElement>();
  const view: View = { start: 0, end: Math.max(1, duration) };
  return (
    <div
      ref={ref}
      data-testid="minimap"
      aria-label={t("pages.moments.minimap")}
      className="relative h-7 cursor-pointer overflow-hidden rounded-md border border-line bg-app"
      onClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const at = xToTime(e.clientX - rect.left, view, rect.width);
        const hit = moments.find((m) => m.start <= at && at <= m.end && m.id === selectedId) ?? moments.find((m) => m.start <= at && at <= m.end);
        if (hit) onSelect(hit.id);
        onSeek(at);
      }}
    >
      {width > 0 &&
        moments.map((m) => (
          <div
            key={m.id}
            data-testid="minimap-moment"
            data-status={m.status}
            title={`${m.title} · ${formatTimecode(m.start)}`}
            className={`absolute top-1 bottom-1 rounded-sm ${STATUS_FILL[m.status]} ${m.id === selectedId ? "ring-2 ring-fg" : ""}`}
            style={{ left: timeToX(m.start, view, width), width: Math.max(2, timeToX(m.end, view, width) - timeToX(m.start, view, width)) }}
          />
        ))}
      {width > 0 && <div className="pointer-events-none absolute inset-y-0 w-px bg-fg" style={{ left: timeToX(time, view, width) }} />}
    </div>
  );
});

function tickStep(span: number): number {
  for (const step of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]) if (span / step <= 10) return step;
  return 600;
}

interface DragState {
  edge: "start" | "end";
  view: View;
  alt: boolean;
}

/**
 * Zoomed timeline around the selected moment (±10 s): waveform, silences (grey), scene cuts
 * (ticks), word boundaries, the playhead and draggable in/out handles. Handles snap to word
 * boundaries while dragging (hold Alt to disable); on release the parent asks snap.py.
 */
export function ZoomTimeline({
  moment,
  duration,
  words,
  analysis,
  waveform,
  time,
  snappedFlash,
  onSeek,
  onPreviewRange,
  onCommitRange,
}: {
  moment: Moment;
  duration: number;
  words: Word[];
  analysis: Analysis | null;
  waveform: Waveform | null;
  time: number;
  snappedFlash: { start: number; end: number } | null;
  onSeek: (t: number) => void;
  onPreviewRange: (start: number, end: number) => void;
  onCommitRange: (start: number, end: number, snap: boolean, edge: "start" | "end") => void;
}) {
  const { t } = useTranslation();
  const [ref, width] = useWidth<HTMLDivElement>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const range = useRef({ start: moment.start, end: moment.end });
  const height = 96;

  const view = drag?.view ?? zoomView(moment.start, moment.end, duration);

  useEffect(() => {
    if (!drag) range.current = { start: moment.start, end: moment.end };
  }, [moment.start, moment.end, drag]);

  // Static layers (waveform, silences, scene cuts, word ticks) -> canvas.
  useEffect(() => {
    const el = canvas.current;
    if (!el || width === 0) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = Math.round(width * dpr);
    el.height = Math.round(height * dpr);
    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const x = (s: number) => timeToX(s, view, width);
    const mid = height / 2 + 6;

    for (const [a, b] of analysis?.silences ?? []) {
      if (b < view.start || a > view.end) continue;
      ctx.fillStyle = "rgba(154,154,165,0.16)";
      ctx.fillRect(x(a), 18, Math.max(1, x(b) - x(a)), height - 18);
    }
    if (waveform && waveform.peaks.length) {
      ctx.fillStyle = "rgba(237,237,237,0.55)";
      const i0 = Math.max(0, Math.floor(view.start * waveform.rate));
      const i1 = Math.min(waveform.peaks.length, Math.ceil(view.end * waveform.rate));
      const barW = Math.max(1, width / Math.max(1, i1 - i0) - 0.5);
      for (let i = i0; i < i1; i++) {
        const amp = (waveform.peaks[i] / 100) * (height / 2 - 14);
        ctx.fillRect(x(i / waveform.rate), mid - amp, barW, Math.max(1, amp * 2));
      }
    }
    ctx.fillStyle = "rgba(255,255,255,0.12)";
    for (const w of words) {
      if (w.end < view.start || w.start > view.end) continue;
      ctx.fillRect(x(w.start), height - 6, 1, 6);
    }
    ctx.fillStyle = "#FFB020";
    for (const cut of analysis?.scene_cuts ?? []) {
      if (cut < view.start || cut > view.end) continue;
      ctx.fillRect(x(cut) - 0.5, 16, 1.5, height - 16);
    }
    // time ruler
    const step = tickStep(view.end - view.start);
    ctx.fillStyle = "rgba(154,154,165,0.9)";
    ctx.font = "10px 'JetBrains Mono', monospace";
    for (let s = Math.ceil(view.start / step) * step; s <= view.end; s += step) {
      ctx.fillRect(x(s), 0, 1, 5);
      ctx.fillText(formatTimecode(s).replace(/\.\d\d$/, ""), x(s) + 3, 11);
    }
  }, [width, view.start, view.end, words, analysis, waveform]);

  const shown = drag ? range.current : { start: moment.start, end: moment.end };
  const left = timeToX(shown.start, view, width);
  const right = timeToX(shown.end, view, width);

  const beginDrag = (edge: "start" | "end") => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    range.current = { start: moment.start, end: moment.end };
    setDrag({ edge, view, alt: e.altKey });
  };

  const moveDrag = (e: React.PointerEvent) => {
    if (!drag || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    let at = xToTime(e.clientX - rect.left, drag.view, rect.width);
    if (!e.altKey) at = snapToWord(at, words, drag.edge);
    const next =
      drag.edge === "start"
        ? clampRange(at, range.current.end, duration, "start")
        : clampRange(range.current.start, at, duration, "end");
    range.current = next;
    setDrag({ ...drag, alt: e.altKey });
    onPreviewRange(next.start, next.end);
  };

  const endDrag = (e: React.PointerEvent) => {
    if (!drag) return;
    const { start, end } = range.current;
    const edge = drag.edge;
    setDrag(null);
    if (Math.abs(start - moment.start) > 1e-4 || Math.abs(end - moment.end) > 1e-4) onCommitRange(start, end, !e.altKey, edge);
  };

  return (
    <div className="select-none">
      <div
        ref={ref}
        data-testid="zoom-timeline"
        data-view-start={view.start}
        data-view-end={view.end}
        className="relative cursor-crosshair overflow-hidden rounded-md border border-line bg-app"
        style={{ height }}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onClick={(e) => {
          if (drag) return;
          const rect = e.currentTarget.getBoundingClientRect();
          onSeek(xToTime(e.clientX - rect.left, view, rect.width));
        }}
      >
        <canvas ref={canvas} className="absolute inset-0" style={{ width, height }} />
        {width > 0 && (
          <>
            <div className="pointer-events-none absolute top-4 bottom-0 bg-accent/15" style={{ left, width: Math.max(1, right - left) }} />
            {(["start", "end"] as const).map((edge) => (
              <div
                key={edge}
                role="slider"
                aria-label={edge === "start" ? t("pages.moments.in_point") : t("pages.moments.out_point")}
                aria-valuenow={edge === "start" ? shown.start : shown.end}
                data-testid={`handle-${edge}`}
                title={`${edge === "start" ? t("pages.moments.in_point") : t("pages.moments.out_point")} · ${t("pages.moments.alt_hint")}`}
                onPointerDown={beginDrag(edge)}
                onClick={(e) => e.stopPropagation()}
                className="absolute top-4 bottom-0 z-10 flex w-3 -translate-x-1/2 cursor-ew-resize justify-center"
                style={{ left: edge === "start" ? left : right }}
              >
                <div className={`h-full w-[3px] rounded-full ${drag?.edge === edge ? "bg-fg" : "bg-accent"}`} />
              </div>
            ))}
            <div className="pointer-events-none absolute inset-y-0 z-20 w-px bg-fg" style={{ left: timeToX(time, view, width) }} />
          </>
        )}
      </div>
      <div className="mt-1 flex h-4 items-center gap-3 text-xs text-muted">
        {drag ? (
          <span className="font-mono">
            {formatTimecode(range.current.start)} → {formatTimecode(range.current.end)}
            {drag.alt ? ` · ${t("pages.moments.snap_off")}` : ""}
          </span>
        ) : snappedFlash ? (
          <span className="font-mono text-success" data-testid="snapped-flash">
            {t("pages.moments.snapped_to", { start: formatTimecode(snappedFlash.start), end: formatTimecode(snappedFlash.end) })}
          </span>
        ) : (
          <span>{t("pages.moments.timeline_legend")}</span>
        )}
      </div>
    </div>
  );
}
