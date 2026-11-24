import { Short } from "@composition/Short";
import { Player, type PlayerRef } from "@remotion/player";
import { useEffect, useMemo, useRef, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { useTranslation } from "react-i18next";

import type { Edl } from "../../lib/api";
import { clipDuration, cropRect, interpolate, SAFE_ZONES, toClipTime, type Region } from "../../lib/edl";

const CANVAS_W = 432; // drawing resolution; the element is scaled with CSS
const CANVAS_H = 768;

/** The sub-rectangle of `r` (normalized, in a w x h frame) with pixel aspect `aspect`, centered. */
function coverRect(r: Region, aspect: number, w: number, h: number): [number, number, number, number] {
  const [rx, ry, rw, rh] = [r[0] * w, r[1] * h, r[2] * w, r[3] * h];
  if (rw / rh > aspect) {
    const nw = rh * aspect;
    return [rx + (rw - nw) / 2, ry, nw, rh];
  }
  const nh = rw / aspect;
  return [rx, ry + (rh - nh) / 2, rw, nh];
}

/** Draw one 9:16 frame of the EDL from the source video (same math as the render). */
export function drawFrame(ctx: CanvasRenderingContext2D, video: HTMLVideoElement, edl: Edl, clipT: number, W: number, H: number) {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  if (!vw || !vh || video.readyState < 2) return;
  const crop = edl.crop;
  if (crop.mode === "split" && crop.split) {
    const topH = Math.round(H * crop.split.ratio);
    const top = coverRect(crop.split.top, W / topH, vw, vh);
    const bottom = coverRect(crop.split.bottom, W / (H - topH), vw, vh);
    ctx.drawImage(video, ...top, 0, 0, W, topH);
    ctx.drawImage(video, ...bottom, 0, topH, W, H - topH);
    return;
  }
  if (crop.fit === "blur") {
    // whole frame centered over a blurred, enlarged copy of itself
    const scale = Math.max(W / vw, H / vh) * 1.08;
    ctx.save();
    ctx.filter = `blur(${Math.round((45 * W) / 1080)}px) brightness(0.7)`;
    ctx.drawImage(video, (W - vw * scale) / 2, (H - vh * scale) / 2, vw * scale, vh * scale);
    ctx.restore();
    const fit = Math.min(W / vw, H / vh);
    ctx.drawImage(video, (W - vw * fit) / 2, (H - vh * fit) / 2, vw * fit, vh * fit);
    return;
  }
  const [x, y, zoom] = interpolate(crop.keyframes, clipT, crop.easing);
  const [l, t, w, h] = cropRect(x, y, zoom, vw, vh, edl.output.w / edl.output.h);
  ctx.drawImage(video, l * vw, t * vh, w * vw, h * vh, 0, 0, W, H);
}

export interface Guide {
  key: "captions" | "hook";
  y: number;
  label: string;
}

/**
 * 9:16 preview. The framed video is drawn on a canvas from the editor's <video> (one decoder, so
 * both viewers share the playhead); captions, hook and progress bar come from the Remotion
 * composition used for the render, in a @remotion/player kept on the same frame. Edits to the
 * EDL show up on the next animation frame.
 */
export function PreviewCanvas({
  videoRef,
  edl,
  showSafeZones,
  language,
  srcW,
  srcH,
  guides = [],
  onGuide,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  edl: Edl;
  showSafeZones: boolean;
  language: string;
  srcW: number;
  srcH: number;
  guides?: Guide[];
  onGuide?: (key: Guide["key"], y: number, phase: "start" | "move" | "end") => void;
}) {
  const { t } = useTranslation();
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const player = useRef<PlayerRef>(null);
  const edlRef = useRef(edl);
  const drawn = useRef<{ time: number; edl: Edl | null; frame: number }>({ time: -1, edl: null, frame: -1 });
  const dragging = useRef<Guide["key"] | null>(null);
  edlRef.current = edl;

  const fps = edl.output.fps;
  const durationInFrames = Math.max(1, Math.round(clipDuration(edl.segments) * fps));
  const inputProps = useMemo(
    () => ({ edl, video: { src: null, width: srcW, height: srcH }, language, showVideo: false }),
    [edl, language, srcW, srcH],
  );

  useEffect(() => {
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const video = videoRef.current;
      const el = canvas.current;
      if (!video || !el) return;
      const current = edlRef.current;
      const time = video.currentTime;
      const clipT = toClipTime(current.segments, time) ?? 0;
      // the frame the <video> is showing (CFR proxy): floor, like the render samples frame n at n/fps
      const frame = Math.min(durationInFrames - 1, Math.max(0, Math.floor(clipT * current.output.fps + 1e-6)));
      if (frame !== drawn.current.frame && player.current) {
        player.current.seekTo(frame);
        drawn.current.frame = frame;
      }
      if (drawn.current.time === time && drawn.current.edl === current && video.readyState >= 2) return;
      const ctx = el.getContext("2d");
      if (!ctx) return;
      drawFrame(ctx, video, current, clipT, el.width, el.height);
      drawn.current = { ...drawn.current, time: video.readyState >= 2 ? time : -1, edl: current };
      el.dataset.time = time.toFixed(3); // for the smoke test
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [videoRef, durationInFrames]);

  const yAt = (e: { clientY: number }) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return 0.5;
    return Math.min(0.95, Math.max(0.05, Math.round(((e.clientY - r.top) / r.height) * 1000) / 1000));
  };
  const down = (key: Guide["key"]) => (e: ReactPointerEvent) => {
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging.current = key;
    onGuide?.(key, yAt(e), "start");
  };
  const move = (e: ReactPointerEvent) => dragging.current && onGuide?.(dragging.current, yAt(e), "move");
  const up = (e: ReactPointerEvent) => {
    if (!dragging.current) return;
    onGuide?.(dragging.current, yAt(e), "end");
    dragging.current = null;
  };

  const zones = SAFE_ZONES[edl.platform] ?? [];
  return (
    <div ref={box} className="relative aspect-[9/16] h-full max-h-full overflow-hidden rounded-lg bg-black ring-1 ring-line" data-testid="preview-916">
      <canvas ref={canvas} width={CANVAS_W} height={CANVAS_H} className="absolute inset-0 size-full" data-testid="preview-canvas" />
      <div className="pointer-events-none absolute inset-0" data-testid="preview-overlay">
        <Player
          ref={player}
          component={Short}
          inputProps={inputProps}
          durationInFrames={durationInFrames}
          compositionWidth={edl.output.w}
          compositionHeight={edl.output.h}
          fps={fps}
          controls={false}
          clickToPlay={false}
          doubleClickToFullscreen={false}
          spaceKeyToPlayOrPause={false}
          acknowledgeRemotionLicense
          style={{ width: "100%", height: "100%", backgroundColor: "transparent" }}
        />
      </div>
      {showSafeZones && (
        <div className="pointer-events-none absolute inset-0" data-testid="safe-zones" aria-label={t("pages.editor.safe_zones")}>
          {zones.map((z, i) => (
            <div
              key={i}
              className="absolute border border-dashed border-warning/70 bg-warning/15"
              style={{ left: `${z[0] * 100}%`, top: `${z[1] * 100}%`, width: `${z[2] * 100}%`, height: `${z[3] * 100}%` }}
            />
          ))}
        </div>
      )}
      {guides.map((g) => (
        <div
          key={g.key}
          role="slider"
          aria-label={g.label}
          aria-valuemin={5}
          aria-valuemax={95}
          aria-valuenow={Math.round(g.y * 100)}
          tabIndex={0}
          data-testid={`guide-${g.key}`}
          className="group absolute right-0 left-0 h-4 -translate-y-1/2 cursor-ns-resize"
          style={{ top: `${g.y * 100}%` }}
          onPointerDown={down(g.key)}
          onPointerMove={move}
          onPointerUp={up}
          onKeyDown={(e) => {
            if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
            e.preventDefault();
            e.stopPropagation();
            const next = Math.min(0.95, Math.max(0.05, g.y + (e.key === "ArrowUp" ? -0.01 : 0.01)));
            onGuide?.(g.key, Math.round(next * 1000) / 1000, "end");
          }}
        >
          <div className="absolute top-1/2 right-0 left-0 border-t border-dashed border-accent/80 group-hover:border-accent" />
          <span className="absolute top-1/2 right-1 -translate-y-1/2 rounded bg-accent px-1.5 py-px text-[11px] font-medium text-white">{g.label}</span>
        </div>
      ))}
    </div>
  );
}
