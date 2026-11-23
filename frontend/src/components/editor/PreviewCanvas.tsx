import { useEffect, useRef, type RefObject } from "react";
import { useTranslation } from "react-i18next";

import type { Edl } from "../../lib/api";
import { cropRect, interpolate, SAFE_ZONES, toClipTime, type Region } from "../../lib/edl";

const CANVAS_W = 432; // drawing resolution (x2 on HiDPI); the element is scaled with CSS
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
    const scale = Math.max(W / vw, H / vh);
    ctx.save();
    ctx.filter = "blur(18px) brightness(0.7)";
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

/**
 * 9:16 preview drawn from the editor's <video> element. Redraws whenever the video time or the
 * EDL changes, so edits show up on the next animation frame.
 */
export function PreviewCanvas({
  videoRef,
  edl,
  showSafeZones,
}: {
  videoRef: RefObject<HTMLVideoElement | null>;
  edl: Edl;
  showSafeZones: boolean;
}) {
  const { t } = useTranslation();
  const canvas = useRef<HTMLCanvasElement>(null);
  const edlRef = useRef(edl);
  const drawn = useRef<{ time: number; edl: Edl | null }>({ time: -1, edl: null });
  edlRef.current = edl;

  useEffect(() => {
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const video = videoRef.current;
      const el = canvas.current;
      if (!video || !el) return;
      const current = edlRef.current;
      const time = video.currentTime;
      if (drawn.current.time === time && drawn.current.edl === current && video.readyState >= 2) return;
      const ctx = el.getContext("2d");
      if (!ctx) return;
      const clipT = toClipTime(current.segments, time) ?? 0;
      drawFrame(ctx, video, current, clipT, el.width, el.height);
      drawn.current = { time: video.readyState >= 2 ? time : -1, edl: current };
      el.dataset.time = time.toFixed(3); // for the smoke test
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [videoRef]);

  const zones = SAFE_ZONES[edl.platform] ?? [];
  return (
    <div className="relative aspect-[9/16] h-full max-h-full overflow-hidden rounded-lg bg-black ring-1 ring-line" data-testid="preview-916">
      <canvas ref={canvas} width={CANVAS_W} height={CANVAS_H} className="size-full" data-testid="preview-canvas" />
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
    </div>
  );
}
