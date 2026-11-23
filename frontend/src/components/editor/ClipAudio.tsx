import { useEffect, useRef, type RefObject } from "react";

import { voiceAudioUrl, type Edl } from "../../lib/api";
import { toClipTime } from "../../lib/edl";

const gain = (db: number) => Math.min(1, Math.max(0, Math.pow(10, db / 20)));

/** Keep `el` playing at `want` seconds while the editor plays; parked there while paused. */
function follow(el: HTMLAudioElement, want: number, playing: boolean) {
  if (playing) {
    if (el.paused) {
      el.currentTime = want;
      void el.play().catch(() => undefined);
    } else if (Math.abs(el.currentTime - want) > 0.2) {
      el.currentTime = want;
    }
  } else {
    if (!el.paused) el.pause();
    if (Math.abs(el.currentTime - want) > 0.05) el.currentTime = want;
  }
}

/**
 * Preview audio for the Clip Editor, driven by the single editor <video>:
 * - the voiceover plays from `voiceover.start` (clip time) and ducks the original by `duck_db`;
 * - with Voice Isolator on, the cleaned voice (source time from `isolated_start`) replaces the
 *   video's own audio;
 * - the original's volume follows `audio.volume_db` (the render applies the same gains, then
 *   loudness normalization).
 */
export function ClipAudio({ videoRef, edl }: { videoRef: RefObject<HTMLVideoElement | null>; edl: Edl }) {
  const voice = useRef<HTMLAudioElement>(null);
  const iso = useRef<HTMLAudioElement>(null);
  const state = useRef<HTMLDivElement>(null);
  const edlRef = useRef(edl);
  edlRef.current = edl;

  const voiceSrc = edl.voiceover.enabled ? voiceAudioUrl(edl.voiceover.audio_path) : null;
  const isoSrc = edl.audio.isolate_voice ? voiceAudioUrl(edl.audio.isolated_path) : null;

  useEffect(() => {
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const v = videoRef.current;
      const e = edlRef.current;
      if (!v) return;
      const playing = !v.paused && !v.ended;
      const clipT = toClipTime(e.segments, v.currentTime);

      // voiceover
      const vo = e.voiceover;
      const vEl = voice.current;
      const voDur = vo.duration ?? (vEl && Number.isFinite(vEl.duration) ? vEl.duration : 0);
      const voOn = !!(vEl && vEl.src && vo.enabled && vo.audio_path && clipT !== null && clipT >= vo.start && clipT < vo.start + voDur);
      if (vEl) {
        if (voOn) follow(vEl, clipT! - vo.start, playing);
        else if (!vEl.paused) vEl.pause();
        vEl.volume = gain(vo.volume_db);
      }
      const original = gain(e.audio.volume_db + (voOn ? vo.duck_db : 0));

      // isolated voice instead of the video's audio
      const iEl = iso.current;
      const isoT = v.currentTime - e.audio.isolated_start;
      const isoOn = !!(iEl && iEl.src && e.audio.isolate_voice && e.audio.isolated_path && Number.isFinite(iEl.duration) && isoT >= 0 && isoT < iEl.duration);
      if (iEl) {
        if (isoOn) follow(iEl, isoT, playing);
        else if (!iEl.paused) iEl.pause();
        iEl.volume = original;
      }
      v.muted = isoOn;
      v.volume = original;

      const el = state.current;
      if (el) {
        // read by the smoke test
        el.dataset.voice = voOn ? "on" : "off";
        el.dataset.isolated = isoOn ? "on" : "off";
        el.dataset.volume = original.toFixed(3);
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      voice.current?.pause();
      iso.current?.pause();
    };
  }, [videoRef]);

  return (
    <div ref={state} hidden data-testid="clip-audio">
      <audio ref={voice} src={voiceSrc ?? undefined} preload="auto" data-testid="voiceover-audio" />
      <audio ref={iso} src={isoSrc ?? undefined} preload="auto" />
    </div>
  );
}
