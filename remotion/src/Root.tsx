import { Composition } from "remotion";

import { compositionSchema, type CompositionProps } from "./schema";
import { durationInFrames, Short } from "./Short";

/** Sample props for Remotion Studio (no video: shows the overlays on black). */
const sample: CompositionProps = {
  edl: {
    segments: [{ start: 0, end: 6 }],
    crop: { mode: "manual", easing: "ease_in_out", fit: "fill", keyframes: [{ t: 0, x: 0.5, y: 0.5, zoom: 1 }], split: null },
    captions: {
      enabled: true,
      style: "bold",
      position_y: 0.72,
      words_per_page: 3,
      font_size: 72,
      words: [
        { start: 0.2, end: 0.6, text: "Xin" },
        { start: 0.6, end: 1.0, text: "chào" },
        { start: 1.0, end: 1.6, text: "các", emphasis: true },
        { start: 1.7, end: 2.2, text: "bạn." },
        { start: 2.6, end: 3.0, text: "Hôm" },
        { start: 3.0, end: 3.4, text: "nay" },
        { start: 3.4, end: 4.0, text: "đặc" },
        { start: 4.0, end: 4.6, text: "biệt!" },
      ],
    },
    hook_text: { enabled: true, text: "Điều bất ngờ nhất hôm nay", start: 0, end: 3.5, position_y: 0.18 },
    progress_bar: { enabled: true, color: "#FFD400" },
    audio: { volume_db: 0 },
    output: { w: 1080, h: 1920, fps: 30 },
  },
  video: { src: null, width: 1920, height: 1080 },
  language: "vi",
  showVideo: true,
};

export function Root() {
  return (
    <Composition
      id="Short"
      component={Short}
      schema={compositionSchema}
      defaultProps={sample}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={durationInFrames(sample)}
      calculateMetadata={({ props }) => ({
        durationInFrames: durationInFrames(props),
        width: props.edl.output.w,
        height: props.edl.output.h,
        fps: props.edl.output.fps,
      })}
    />
  );
}
