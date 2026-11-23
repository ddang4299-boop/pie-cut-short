import { AbsoluteFill } from "remotion";

import { FONT_FAMILY, useFontsReady } from "./fonts";
import { clipDuration } from "./lib/edl";
import { Captions } from "./layers/Captions";
import { Hook, ProgressBar } from "./layers/Overlays";
import { VideoLayer } from "./layers/VideoLayer";
import type { CompositionProps } from "./schema";

/**
 * One 9:16 short from an EDL: framed source video, word-level captions, hook text and a progress
 * bar. The Clip Editor shows the same component in @remotion/player (with `showVideo: false` it
 * draws only the overlays on top of the editor's own video preview).
 */
export function Short(props: CompositionProps) {
  useFontsReady();
  return (
    <AbsoluteFill style={{ backgroundColor: props.showVideo ? "#000" : "transparent", fontFamily: FONT_FAMILY }}>
      {props.showVideo && props.video.src && <VideoLayer {...props} />}
      <Captions edl={props.edl} language={props.language} />
      <Hook edl={props.edl} />
      <ProgressBar edl={props.edl} />
    </AbsoluteFill>
  );
}

export const durationInFrames = (props: Pick<CompositionProps, "edl">) =>
  Math.max(1, Math.round(clipDuration(props.edl.segments) * props.edl.output.fps));
