import type { CSSProperties } from "react";
import { AbsoluteFill, OffthreadVideo, Sequence, useCurrentFrame, useVideoConfig } from "remotion";

import { cropRect, interpolate, segmentOffsets, type Region } from "../lib/edl";
import type { CompositionProps } from "../schema";

/** Sub-rectangle (source px) of region r with the panel's aspect, centered — same as the editor preview. */
function coverRect(r: Region, aspect: number, w: number, h: number): [number, number, number, number] {
  const [rx, ry, rw, rh] = [r[0] * w, r[1] * h, r[2] * w, r[3] * h];
  if (rw / rh > aspect) {
    const nw = rh * aspect;
    return [rx + (rw - nw) / 2, ry, nw, rh];
  }
  const nh = rw / aspect;
  return [rx, ry + (rh - nh) / 2, rw, nh];
}

/** Style placing the whole source so that source rect (px) [l, t, w, h] fills a box boxW x boxH. */
function place(l: number, t: number, w: number, h: number, srcW: number, srcH: number, boxW: number, boxH: number): CSSProperties {
  const sx = boxW / w;
  const sy = boxH / h;
  return { position: "absolute", left: -l * sx, top: -t * sy, width: srcW * sx, height: srcH * sy, maxWidth: "none" };
}

const volumeOf = (db: number) => Math.min(1, Math.pow(10, db / 20));

function Framed({ props, seg, fromFrame }: { props: CompositionProps; seg: { start: number; end: number }; fromFrame: number }) {
  const frame = useCurrentFrame();
  const { fps, width: W, height: H } = useVideoConfig();
  const { edl, video } = props;
  const src = video.src!;
  const sw = video.width;
  const sh = video.height;
  const clipT = (fromFrame + frame) / fps;
  const trim = { trimBefore: Math.round(seg.start * fps), trimAfter: Math.round(seg.end * fps) };
  const volume = volumeOf(edl.audio.volume_db);
  const crop = edl.crop;

  if (crop.mode === "split" && crop.split) {
    const topH = Math.round(H * crop.split.ratio);
    const top = coverRect(crop.split.top, W / topH, sw, sh);
    const bottom = coverRect(crop.split.bottom, W / (H - topH), sw, sh);
    return (
      <AbsoluteFill>
        <div style={{ position: "absolute", left: 0, top: 0, width: W, height: topH, overflow: "hidden" }}>
          <OffthreadVideo src={src} {...trim} volume={volume} style={place(...top, sw, sh, W, topH)} />
        </div>
        <div style={{ position: "absolute", left: 0, top: topH, width: W, height: H - topH, overflow: "hidden" }}>
          <OffthreadVideo src={src} {...trim} muted style={place(...bottom, sw, sh, W, H - topH)} />
        </div>
      </AbsoluteFill>
    );
  }
  if (crop.fit === "blur") {
    const cover = Math.max(W / sw, H / sh) * 1.08;
    const fit = Math.min(W / sw, H / sh);
    const blur = Math.round((45 * W) / 1080);
    return (
      <AbsoluteFill>
        <OffthreadVideo
          src={src}
          {...trim}
          muted
          style={{ position: "absolute", left: (W - sw * cover) / 2, top: (H - sh * cover) / 2, width: sw * cover, height: sh * cover, maxWidth: "none", filter: `blur(${blur}px) brightness(0.7)` }}
        />
        <OffthreadVideo
          src={src}
          {...trim}
          volume={volume}
          style={{ position: "absolute", left: (W - sw * fit) / 2, top: (H - sh * fit) / 2, width: sw * fit, height: sh * fit, maxWidth: "none" }}
        />
      </AbsoluteFill>
    );
  }
  const [x, y, zoom] = interpolate(crop.keyframes, clipT, crop.easing);
  const [l, t, w, h] = cropRect(x, y, zoom, sw, sh, W / H);
  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      <OffthreadVideo src={src} {...trim} volume={volume} style={place(l * sw, t * sh, w * sw, h * sh, sw, sh, W, H)} />
    </AbsoluteFill>
  );
}

/** The source video, cut to the EDL segments and framed by its crop (fill / blur / split). */
export function VideoLayer(props: CompositionProps) {
  const { fps } = useVideoConfig();
  const offsets = segmentOffsets(props.edl.segments);
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {props.edl.segments.map((seg, i) => {
        const from = Math.round(offsets[i] * fps);
        const to = Math.round((offsets[i] + seg.end - seg.start) * fps);
        return (
          <Sequence key={`${seg.start}-${i}`} from={from} durationInFrames={Math.max(1, to - from)} name={`Segment ${i + 1}`}>
            <Framed props={props} seg={seg} fromFrame={from} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
}
