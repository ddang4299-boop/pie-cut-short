import { X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, apiUrl, getTranscript, type Source, type Transcript } from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { formatTimecode } from "../../lib/format";
import { contentLanguageLabel } from "../../lib/labels";
import { wordIndexAt } from "../../lib/transcript";
import { ErrorState, Skeleton } from "../states";
import { Tooltip } from "../Tooltip";

/** Read-only transcript viewer: proxy video + words grouped by segment, current word highlighted. */
export function TranscriptDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const { t } = useTranslation();
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [current, setCurrent] = useState(-1);
  const videoRef = useRef<HTMLVideoElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setError(null);
    setTranscript(null);
    getTranscript(source.id)
      .then(setTranscript)
      .catch((err) => setError(err instanceof ApiError ? err : new ApiError({ code: "UNKNOWN", severity: "error", params: {} }, 0)));
  }, [source.id]);

  useEffect(load, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // Follow playback with requestAnimationFrame (timeupdate only fires ~4x per second).
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !transcript) return;
    let frame = 0;
    const tick = () => {
      setCurrent(wordIndexAt(transcript.words, video.currentTime));
      if (!video.paused) frame = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    video.addEventListener("play", onPlay);
    video.addEventListener("seeked", tick);
    return () => {
      cancelAnimationFrame(frame);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("seeked", tick);
    };
  }, [transcript]);

  useEffect(() => {
    if (current < 0) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-word="${current}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [current]);

  const seek = (time: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = time;
    void video.play().catch(() => undefined);
  };

  const segments = useMemo(() => transcript?.segments ?? [], [transcript]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("pages.transcript.title")}
        data-testid="transcript-dialog"
        className="flex h-[85vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-2xl shadow-black/60"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line px-5">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">
              {t("pages.transcript.title")} · {source.name}
            </h2>
            {transcript && (
              <p className="truncate text-xs text-muted">
                {t("pages.transcript.meta", { words: transcript.words.length, model: transcript.model, device: transcript.device.toUpperCase() })}
                {" · "}
                {t("pages.transcript.language", { language: contentLanguageLabel(t, transcript.language) })}
              </p>
            )}
          </div>
          <Tooltip label={t("common.close")} shortcut="Esc" side="bottom-end">
            <button
              type="button"
              onClick={onClose}
              aria-label={t("common.close")}
              className="rounded-md p-1.5 text-muted hover:bg-white/5 hover:text-fg"
            >
              <X className="size-4" />
            </button>
          </Tooltip>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-3 border-r border-line bg-app p-4">
            {source.proxy_url ? (
              <video
                ref={videoRef}
                src={apiUrl(source.proxy_url)}
                controls
                preload="metadata"
                data-testid="transcript-video"
                className="aspect-video w-full rounded-lg bg-black"
              />
            ) : (
              <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-black text-xs text-muted">
                {t("pages.transcript.no_proxy")}
              </div>
            )}
            <p className="text-xs text-muted">{t("pages.transcript.hint")}</p>
          </div>

          <div ref={listRef} className="min-h-0 overflow-y-auto p-5" lang={transcript?.language === "vi" ? "vi" : undefined}>
            {error && <ErrorState message={translateIssue(t, error.issue)} onRetry={load} />}
            {!error && !transcript && <Skeleton rows={8} />}
            {transcript && transcript.words.length === 0 && <p className="text-sm text-muted">{t("pages.transcript.empty")}</p>}
            {transcript && (
              <div className="space-y-4" data-testid="transcript-segments">
                {segments.map((segment, index) => {
                  const words = transcript.words.slice(segment.first_word, segment.last_word + 1);
                  return (
                    <div key={index} className="flex gap-3">
                      <button
                        type="button"
                        onClick={() => seek(segment.start)}
                        className="shrink-0 font-mono text-[11px] leading-7 text-muted hover:text-accent"
                      >
                        {formatTimecode(segment.start)}
                      </button>
                      <p className="text-sm leading-7">
                        {words.map((word, offset) => {
                          const wordIndex = segment.first_word + offset;
                          const active = wordIndex === current;
                          return (
                            <span key={wordIndex}>
                              <span
                                role="button"
                                tabIndex={0}
                                data-word={wordIndex}
                                onClick={() => seek(word.start)}
                                onKeyDown={(e) => e.key === "Enter" && seek(word.start)}
                                className={`-mx-0.5 cursor-pointer rounded px-0.5 transition-colors ${
                                  active ? "bg-accent text-white" : "hover:bg-white/10"
                                }`}
                              >
                                {word.text}
                              </span>{" "}
                            </span>
                          );
                        })}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
