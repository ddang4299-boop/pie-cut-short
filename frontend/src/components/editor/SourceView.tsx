import { forwardRef, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Edl } from "../../lib/api";
import { clampCenter, cropRect, fitRegion, interpolate, splitAspects, type Region } from "../../lib/edl";

export type Phase = "start" | "move" | "end";
export type SplitPanel = "top" | "bottom";

interface Props {
  src: string | null;
  edl: Edl;
  clipTime: number;
  srcW: number;
  srcH: number;
  drawTarget: SplitPanel | null;
  onTogglePlay: () => void;
  onCrop: (center: { x: number; y: number; zoom: number }, phase: Phase) => void;
  onZoomWheel: (zoom: number) => void;
  onSplit: (panel: SplitPanel, region: Region, phase: Phase) => void;
}

/** Content rectangle of an object-contain video inside its box (px). */
function contentRect(boxW: number, boxH: number, aspect: number) {
  if (boxW / boxH > aspect) {
    const w = boxH * aspect;
    return { left: (boxW - w) / 2, top: 0, width: w, height: boxH };
  }
  const h = boxW / aspect;
  return { left: 0, top: (boxH - h) / 2, width: boxW, height: h };
}

const pct = (v: number) => `${v * 100}%`;

/**
 * 16:9 source viewer (the editor's only <video>, so both viewers share one playhead) with the
 * crop overlay: drag the box to move the framing (keyframe at the playhead), scroll to zoom.
 * In split mode the two source regions are moved/resized here, or drawn after "Draw".
 */
export const SourceView = forwardRef<HTMLVideoElement, Props>(function SourceView(
  { src, edl, clipTime, srcW, srcH, drawTarget, onTogglePlay, onCrop, onZoomWheel, onSplit },
  videoRef,
) {
  const { t } = useTranslation();
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const drag = useRef<{
    kind: "crop" | "move" | "resize" | "draw";
    panel?: SplitPanel;
    x0: number;
    y0: number;
    start: { x: number; y: number; zoom: number } | Region;
  } | null>(null);
  const overlay = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const aspect = srcW / srcH;
  const rect = contentRect(size.w || 1, size.h || 1, aspect);
  const outAspect = edl.output.w / edl.output.h;
  const crop = edl.crop;
  const split = crop.mode === "split" ? crop.split : null;
  const [x, y, zoom] = interpolate(crop.keyframes, clipTime, crop.easing);
  const [cl, ct, cw, ch] = cropRect(x, y, zoom, srcW, srcH, outAspect);

  const norm = (e: { clientX: number; clientY: number }) => {
    const r = overlay.current?.getBoundingClientRect();
    if (!r) return { nx: 0, ny: 0 };
    return { nx: (e.clientX - r.left) / r.width, ny: (e.clientY - r.top) / r.height };
  };

  const aspectFor = (panel: SplitPanel) => {
    const [topA, bottomA] = splitAspects(edl.output.w, edl.output.h, split?.ratio ?? 0.4);
    return panel === "top" ? topA : bottomA;
  };

  const begin = (e: ReactPointerEvent, d: NonNullable<typeof drag.current>) => {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = d;
  };

  const onMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const { nx, ny } = norm(e);
    const dx = nx - d.x0;
    const dy = ny - d.y0;
    if (d.kind === "crop") {
      const s = d.start as { x: number; y: number; zoom: number };
      const [cx, cy] = clampCenter(s.x + dx, s.y + dy, s.zoom, srcW, srcH, outAspect);
      onCrop({ x: cx, y: cy, zoom: s.zoom }, "move");
    } else if (d.panel) {
      const r = d.start as Region;
      const a = aspectFor(d.panel);
      if (d.kind === "move") {
        onSplit(d.panel, fitRegion(r[0] + r[2] / 2 + dx, r[1] + r[3] / 2 + dy, r[2], a, srcW, srcH), "move");
      } else if (d.kind === "resize") {
        const w = Math.max(0.05, r[2] + dx);
        // keep the top-left corner fixed while resizing
        const region = fitRegion(r[0] + w / 2, r[1] + ((w * srcW) / a / srcH) / 2, w, a, srcW, srcH);
        onSplit(d.panel, region, "move");
      } else {
        const w = Math.max(0.04, Math.abs(dx));
        onSplit(d.panel, fitRegion((d.x0 + nx) / 2, (d.y0 + ny) / 2, w, a, srcW, srcH), "move");
      }
    }
  };

  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.kind === "crop") onCrop({ x, y, zoom }, "end");
    else if (d.panel) onSplit(d.panel, (split?.[d.panel] ?? (d.start as Region)) as Region, "end");
  };

  return (
    <div ref={box} className="relative aspect-video w-full overflow-hidden rounded-lg bg-black" data-testid="source-view">
      {src ? (
        <video ref={videoRef} src={src} preload="auto" playsInline className="size-full object-contain" onClick={onTogglePlay} data-testid="editor-video" />
      ) : (
        <div className="flex size-full items-center justify-center text-sm text-muted">{t("pages.moments.no_proxy")}</div>
      )}
      {src && size.w > 0 && (
        <div
          ref={overlay}
          className={`absolute ${drawTarget ? "cursor-crosshair" : "pointer-events-none"}`}
          style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
          onPointerDown={(e) => {
            if (!drawTarget) return;
            const { nx, ny } = norm(e);
            begin(e, { kind: "draw", panel: drawTarget, x0: nx, y0: ny, start: [nx, ny, 0, 0] });
          }}
          onPointerMove={onMove}
          onPointerUp={onUp}
          data-testid="crop-overlay"
        >
          {split ? (
            (["top", "bottom"] as SplitPanel[]).map((panel) => {
              const r = split[panel];
              return (
                <div
                  key={panel}
                  data-testid={`split-${panel}`}
                  className={`pointer-events-auto absolute cursor-move border-2 ${panel === "top" ? "border-accent bg-accent/10" : "border-success bg-success/10"}`}
                  style={{ left: pct(r[0]), top: pct(r[1]), width: pct(r[2]), height: pct(r[3]) }}
                  onPointerDown={(e) => {
                    const { nx, ny } = norm(e);
                    begin(e, { kind: "move", panel, x0: nx, y0: ny, start: r });
                  }}
                  onPointerMove={onMove}
                  onPointerUp={onUp}
                >
                  <span className={`absolute top-1 left-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-white ${panel === "top" ? "bg-accent" : "bg-success/90"}`}>
                    {t(`pages.editor.split_${panel}`)}
                  </span>
                  <span
                    className="absolute -right-1.5 -bottom-1.5 size-3 cursor-nwse-resize rounded-sm border border-white bg-panel"
                    onPointerDown={(e) => {
                      const { nx, ny } = norm(e);
                      begin(e, { kind: "resize", panel, x0: nx, y0: ny, start: r });
                    }}
                    onPointerMove={onMove}
                    onPointerUp={onUp}
                    data-testid={`split-${panel}-resize`}
                  />
                </div>
              );
            })
          ) : crop.fit === "blur" ? (
            <span className="absolute top-2 left-2 rounded bg-black/70 px-2 py-0.5 text-xs text-white">{t("pages.editor.fit_blur_note")}</span>
          ) : (
            <div
              data-testid="crop-box"
              className="pointer-events-auto absolute cursor-move border-2 border-accent shadow-[0_0_0_9999px_rgba(0,0,0,0.55)]"
              style={{ left: pct(cl), top: pct(ct), width: pct(cw), height: pct(ch) }}
              onPointerDown={(e) => {
                const { nx, ny } = norm(e);
                onCrop({ x, y, zoom }, "start");
                begin(e, { kind: "crop", x0: nx, y0: ny, start: { x, y, zoom } });
              }}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onWheel={(e) => {
                const next = Math.min(4, Math.max(1, Math.round((zoom - Math.sign(e.deltaY) * 0.05) * 100) / 100));
                if (next !== zoom) onZoomWheel(next);
              }}
            >
              <span className="absolute top-1 left-1 rounded bg-accent px-1.5 py-0.5 font-mono text-[11px] text-white">{zoom.toFixed(2)}×</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
