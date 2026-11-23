import { useEffect, useRef, type RefObject } from "react";

import { DEFAULT_MUSIC, musicUrl, voiceAudioUrl, type Edl, type EdlMusic } from "../../lib/api";
import { clipDuration, toClipTime } from "../../lib/edl";

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
 * Where the background music is at clip time `clipT` (same rules as the render's audio_mix):
 * it starts at `start`, plays the track from `offset`, wraps to 0 at the track's end when looping
 * (otherwise stops), and fades in/out linearly (fades capped at half the music's length).
 */
export function musicAt(m: EdlMusic, trackDuration: number, total: number, clipT: number): { trackT: number; fade: number } | null {
  if (!m.enabled || !m.track_id || trackDuration <= 0) return null;
  const end = m.loop ? total : Math.min(total, m.start + Math.max(0, trackDuration - m.offset));
  const len = end - m.start;
  const elapsed = clipT - m.start;
  if (len <= 0.01 || elapsed < 0 || clipT >= end) return null;
  const trackT = m.loop ? (m.offset + elapsed) % trackDuration : m.offset + elapsed;
  const fadeIn = Math.min(m.fade_in, len / 2);
  const fadeOut = Math.min(m.fade_out, len / 2);
  let fade = 1;
  if (fadeIn > 0.01 && elapsed < fadeIn) fade = elapsed / fadeIn;
  if (fadeOut > 0.01 && elapsed > len - fadeOut) fade = Math.min(fade, (len - elapsed) / fadeOut);
  return { trackT, fade: Math.max(0, Math.min(1, fade)) };
}

/**
 * Preview audio for the Clip Editor, driven by the single editor <video>:
 * - the voiceover plays from `voiceover.start` (clip time) and ducks the original by `duck_db`;
 * - with Voice Isolator on, the cleaned voice (source time from `isolated_start`) replaces the
 *   video's own audio;
 * - the original's volume follows `audio.volume_db` (the render applies the same gains, then
 *   loudness normalization);
 * - background music follows `musicAt()`, ducked by `music.duck_db` while the voiceover plays.
 */
export function ClipAudio({ videoRef, edl }: { videoRef: RefObject<HTMLVideoElement | null>; edl: Edl }) {
  const voice = useRef<HTMLAudioElement>(null);
  const iso = useRef<HTMLAudioElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  const state = useRef<HTMLDivElement>(null);
  const edlRef = useRef(edl);
  edlRef.current = edl;

  const voiceSrc = edl.voiceover.enabled ? voiceAudioUrl(edl.voiceover.audio_path) : null;
  const isoSrc = edl.audio.isolate_voice ? voiceAudioUrl(edl.audio.isolated_path) : null;
  const musicSrc = edl.music?.enabled ? musicUrl(edl.music.track_id) : null;

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

      // background music
      const mu = e.music ?? DEFAULT_MUSIC;
      const mEl = music.current;
      const mDur = mu.duration ?? (mEl && Number.isFinite(mEl.duration) ? mEl.duration : 0);
      const at = mEl && mEl.src && clipT !== null ? musicAt(mu, mDur, clipDuration(e.segments), clipT) : null;
      if (mEl) {
        if (at) follow(mEl, at.trackT, playing);
        else if (!mEl.paused) mEl.pause();
        mEl.volume = at ? gain(mu.volume_db + (voOn ? mu.duck_db : 0)) * at.fade : 0;
      }

      const el = state.current;
      if (el) {
        // read by the smoke test
        el.dataset.voice = voOn ? "on" : "off";
        el.dataset.isolated = isoOn ? "on" : "off";
        el.dataset.volume = original.toFixed(3);
        el.dataset.music = at ? "on" : "off";
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      voice.current?.pause();
      iso.current?.pause();
      music.current?.pause();
    };
  }, [videoRef]);

  return (
    <div ref={state} hidden data-testid="clip-audio">
      <audio ref={voice} src={voiceSrc ?? undefined} preload="auto" data-testid="voiceover-audio" />
      <audio ref={iso} src={isoSrc ?? undefined} preload="auto" />
      <audio ref={music} src={musicSrc ?? undefined} preload="auto" data-testid="music-audio" />
    </div>
  );
}
