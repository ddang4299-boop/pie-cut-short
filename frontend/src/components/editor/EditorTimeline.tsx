import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useTranslation } from "react-i18next";

import type { Edl, Waveform } from "../../lib/api";
import { clipDuration, segmentOffsets, sortKeyframes, toSourceTime } from "../../lib/edl";
import { pagesOf } from "../../lib/captions";
import { formatTimecode } from "../../lib/format";

const LABEL_W = 104;
const TRACK_H = 28;
const MIN_PPS = 4;
const MAX_PPS = 400;

function rulerStep(pps: number): number {
  for (const step of [0.5, 1, 2, 5, 10, 15, 30, 60]) if (step * pps >= 64) return step;
  return 120;
}

/**
 * Multi-track timeline in clip time: Video (segments; S splits, Delete removes the selected one),
 * Captions, Hook, Voiceover, Audio (waveform) and Crop (keyframe dots). Click or drag to seek;
 * Ctrl+scroll zooms.
 */
export function EditorTimeline({
  edl,
  time,
  waveform,
  selectedSegment,
  selectedKeyframe,
  onSeek,
  onSelectSegment,
  onSelectKeyframe,
  onVoiceStart,
}: {
  edl: Edl;
  time: number;
  waveform: Waveform | null;
  selectedSegment: number | null;
  selectedKeyframe: number | null;
  onSeek: (clipT: number) => void;
  onSelectSegment: (index: number | null) => void;
  onSelectKeyframe: (index: number | null) => void;
  /** Drag the voiceover block: live moves, then one "end" (one undo step). */
  onVoiceStart?: (start: number, phase: "start" | "move" | "end") => void;
}) {
  const { t } = useTranslation();
  const scroller = useRef<HTMLDivElement>(null);
  const wave = useRef<HTMLCanvasElement>(null);
  const [viewW, setViewW] = useState(800);
  const [pps, setPps] = useState<number | null>(null); // null = fit to width
  const duration = Math.max(0.1, clipDuration(edl.segments));
  const pages = useMemo(() => pagesOf(edl.captions.words, edl.segments, edl.captions.words_per_page), [edl.captions.words, edl.segments, edl.captions.words_per_page]);
  const fitPps = Math.max(MIN_PPS, (viewW - 24) / duration);
  const scale = pps ?? fitPps;
  const width = Math.max(viewW - 8, duration * scale + 16);
  const offsets = useMemo(() => segmentOffsets(edl.segments), [edl.segments]);
  const keyframes = useMemo(() => sortKeyframes(edl.crop.keyframes), [edl.crop.keyframes]);
  const scrub = useRef(false);
  const voDrag = useRef<{ x: number; start: number; moved: boolean } | null>(null);
  const vo = edl.voiceover;
  const voDur = vo.duration ?? 0;
  // background music span in clip time (shown even when disabled, struck through)
  const music = useMemo(() => {
    const m = edl.music;
    if (!m?.track_id || !m.duration) return null;
    const end = m.loop ? duration : Math.min(duration, m.start + Math.max(0, m.duration - m.offset));
    return end > m.start + 0.01 ? { start: m.start, end } : null;
  }, [edl.music, duration]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setViewW(entry.contentRect.width));
    ro.observe(el);
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setPps((prev) => {
        const base = prev ?? (el.clientWidth - 24) / duration;
        return Math.min(MAX_PPS, Math.max(MIN_PPS, base * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      ro.disconnect();
      el.removeEventListener("wheel", onWheel);
    };
  }, [duration]);

  // keep the playhead in view while playing / stepping
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const x = time * scale;
    if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, x - el.clientWidth / 3);
  }, [time, scale]);

  // waveform of the clip (source peaks mapped through the segments)
  useEffect(() => {
    const canvas = wave.current;
    if (!canvas) return;
    const w = Math.min(16000, Math.ceil(duration * scale));
    canvas.width = Math.max(1, w);
    canvas.height = TRACK_H * 2;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!waveform || !waveform.peaks.length) return;
    ctx.fillStyle = "rgba(237,237,237,0.45)";
    const mid = canvas.height / 2;
    for (let px = 0; px < w; px++) {
      const src = toSourceTime(edl.segments, (px / w) * duration);
      const peak = waveform.peaks[Math.min(waveform.peaks.length - 1, Math.floor(src * waveform.rate))] ?? 0;
      const h = Math.max(1, peak * mid * 0.95);
      ctx.fillRect(px, mid - h, 1, h * 2);
    }
  }, [waveform, edl.segments, duration, scale]);

  const timeAt = (e: { clientX: number }) => {
    const el = scroller.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    return Math.min(duration, Math.max(0, (e.clientX - r.left + el.scrollLeft - 8) / scale));
  };
  const down = (e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    scrub.current = true;
    onSeek(timeAt(e));
  };
  const move = (e: ReactPointerEvent) => scrub.current && onSeek(timeAt(e));
  const up = () => {
    scrub.current = false;
  };

  const step = rulerStep(scale);
  const ticks: number[] = [];
  for (let s = 0; s <= duration + 1e-6; s += step) ticks.push(s);
  const tracks = ["video", "captions", "hook", "voiceover", "music", "audio", "crop"] as const;
  const x = (s: number) => 8 + s * scale;

  return (
    <div className="flex h-full min-h-0 border-t border-line bg-panel" data-testid="editor-timeline">
      <div className="shrink-0 border-r border-line pt-6" style={{ width: LABEL_W }}>
        {tracks.map((tr) => (
          <div key={tr} className="flex items-center truncate px-3 text-xs text-muted" style={{ height: TRACK_H }} title={t(`pages.editor.track.${tr}`)}>
            {t(`pages.editor.track.${tr}`)}
          </div>
        ))}
      </div>
      <div ref={scroller} className="relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden" data-testid="timeline-scroller">
        <div className="relative" style={{ width }} onPointerDown={down} onPointerMove={move} onPointerUp={up}>
          {/* ruler */}
          <div className="relative h-6 border-b border-line">
            {ticks.map((s) => (
              <span key={s} className="absolute top-1 font-mono text-[11px] text-muted" style={{ left: x(s) }}>
                <span className="absolute -top-1 left-0 h-1.5 w-px bg-line" />
                &nbsp;{formatTimecode(s)}
              </span>
            ))}
          </div>
          {/* video */}
          <div className="relative" style={{ height: TRACK_H }}>
            {edl.segments.map((s, i) => (
              <button
                key={`${s.start}-${i}`}
                type="button"
                data-testid="timeline-segment"
                data-index={i}
                aria-pressed={selectedSegment === i}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => {
                  onSelectSegment(selectedSegment === i ? null : i);
                  onSeek(offsets[i]);
                }}
                className={`absolute top-1 bottom-1 truncate rounded border px-1.5 text-left font-mono text-[11px] ${
                  selectedSegment === i ? "border-accent bg-accent/30 text-fg" : "border-line bg-white/10 text-muted hover:bg-white/15"
                }`}
                style={{ left: x(offsets[i]) + 1, width: Math.max(4, (s.end - s.start) * scale - 2) }}
                title={`${formatTimecode(s.start)} – ${formatTimecode(s.end)}`}
              >
                {formatTimecode(s.start)}
              </button>
            ))}
          </div>
          {/* captions: one block per page */}
          <div className="relative border-t border-line/50" style={{ height: TRACK_H }} data-testid="track-captions">
            {edl.captions.enabled &&
              pages.map((p) => (
                <div
                  key={`${p.words[0].index}-${p.words.length}`}
                  className={`absolute top-1.5 bottom-1.5 truncate rounded border-l-2 px-1 text-[11px] leading-[16px] ${
                    time >= p.start && time < p.show_end ? "border-accent bg-accent/30 text-fg" : "border-accent/60 bg-accent/10 text-muted"
                  }`}
                  style={{ left: x(p.start), width: Math.max(3, (p.show_end - p.start) * scale - 1) }}
                  title={`${formatTimecode(p.start)} – ${formatTimecode(p.show_end)}`}
                  data-content
                >
                  {p.words.map((w) => w.text).join(" ")}
                </div>
              ))}
          </div>
          {/* hook */}
          <div className="relative border-t border-line/50" style={{ height: TRACK_H }}>
            {edl.hook_text.enabled && edl.hook_text.text && (
              <div
                className="absolute top-1.5 bottom-1.5 truncate rounded bg-warning/25 px-1.5 text-[11px] leading-[18px] text-warning"
                style={{ left: x(edl.hook_text.start), width: Math.max(4, (Math.min(duration, edl.hook_text.end) - edl.hook_text.start) * scale) }}
              >
                {edl.hook_text.text}
              </div>
            )}
          </div>
          {/* voiceover: drag to move its start */}
          <div className="relative border-t border-line/50" style={{ height: TRACK_H }} data-testid="track-voiceover">
            {vo.audio_path && voDur > 0 && (
              <div
                role="slider"
                tabIndex={0}
                aria-label={t("pages.editor.voice_block")}
                aria-valuenow={vo.start}
                aria-valuemin={0}
                aria-valuemax={duration}
                data-testid="voiceover-block"
                data-start={vo.start}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  if (e.button !== 0 || !onVoiceStart) return;
                  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                  voDrag.current = { x: e.clientX, start: vo.start, moved: false };
                  onVoiceStart(vo.start, "start");
                }}
                onPointerMove={(e) => {
                  const d = voDrag.current;
                  if (!d || !onVoiceStart) return;
                  if (Math.abs(e.clientX - d.x) > 2) d.moved = true;
                  if (d.moved) onVoiceStart(Math.round(Math.min(Math.max(0, duration - 0.1), Math.max(0, d.start + (e.clientX - d.x) / scale)) * 100) / 100, "move");
                }}
                onPointerUp={() => {
                  const d = voDrag.current;
                  voDrag.current = null;
                  if (!d || !onVoiceStart) return;
                  if (!d.moved) onSeek(vo.start);
                  onVoiceStart(vo.start, "end");
                }}
                onKeyDown={(e) => {
                  if (!onVoiceStart || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
                  e.preventDefault();
                  e.stopPropagation();
                  const delta = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowLeft" ? -1 : 1);
                  const next = Math.round(Math.max(0, Math.min(duration - 0.1, vo.start + delta)) * 100) / 100;
                  onVoiceStart(vo.start, "start");
                  onVoiceStart(next, "move");
                  onVoiceStart(next, "end");
                }}
                className={`absolute top-1.5 bottom-1.5 cursor-ew-resize truncate rounded border select-none px-1.5 text-[11px] leading-[16px] outline-none focus:ring-1 focus:ring-success ${
                  vo.enabled ? "border-success/70 bg-success/20 text-success" : "border-line bg-white/5 text-muted line-through"
                }`}
                style={{ left: x(vo.start), width: Math.max(6, voDur * scale) }}
                title={`${formatTimecode(vo.start)} – ${formatTimecode(vo.start + voDur)}`}
                data-content
              >
                {vo.script}
              </div>
            )}
          </div>
          {/* background music: click to jump to its start */}
          <div className="relative border-t border-line/50" style={{ height: TRACK_H }} data-testid="track-music">
            {music && (
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => onSeek(music.start)}
                data-testid="music-block"
                className={`absolute top-1.5 bottom-1.5 truncate rounded border px-1.5 text-left text-[11px] leading-[14px] ${
                  edl.music.enabled ? "border-accent/70 bg-accent/20 text-[#c4b5fd]" : "border-line bg-white/5 text-muted line-through"
                }`}
                style={{ left: x(music.start), width: Math.max(6, (music.end - music.start) * scale) }}
                title={`${edl.music.name} · ${formatTimecode(music.start)} – ${formatTimecode(music.end)}`}
              >
                {edl.music.name}
              </button>
            )}
          </div>
          {/* audio */}
          <div className="relative border-t border-line/50" style={{ height: TRACK_H }}>
            <canvas ref={wave} className="absolute top-0" style={{ left: x(0), width: duration * scale, height: TRACK_H }} />
          </div>
          {/* crop keyframes */}
          <div className="relative border-t border-line/50" style={{ height: TRACK_H }} data-testid="timeline-keyframes">
            {edl.crop.mode !== "split" &&
              keyframes.map((k, i) => (
                <button
                  key={`${k.t}-${i}`}
                  type="button"
                  aria-label={t("pages.editor.keyframe_at", { time: formatTimecode(k.t) })}
                  data-testid="timeline-keyframe"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => {
                    onSelectKeyframe(i);
                    onSeek(k.t);
                  }}
                  className={`absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 border ${
                    selectedKeyframe === i ? "border-white bg-accent" : "border-accent bg-panel hover:bg-accent/60"
                  }`}
                  style={{ left: x(k.t) }}
                />
              ))}
          </div>
          {/* segment boundaries + playhead */}
          {offsets.slice(1).map((o, i) => (
            <div key={i} className="pointer-events-none absolute top-6 bottom-0 w-px bg-white/25" style={{ left: x(o) }} />
          ))}
          <div className="pointer-events-none absolute top-0 bottom-0 w-px bg-accent" style={{ left: x(time) }} data-testid="timeline-playhead">
            <div className="absolute -top-0 -left-1 size-2 rotate-45 bg-accent" />
          </div>
        </div>
      </div>
    </div>
  );
}
