import { Crosshair, Loader2, Music2, Pause, Play, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  DEFAULT_MUSIC,
  apiUrl,
  deleteMusic,
  listMusic,
  uploadMusic,
  type ApiIssue,
  type Edl,
  type EdlMusic,
  type MusicTrack,
} from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { formatDuration } from "../../lib/format";
import { ConfirmDialog } from "../ConfirmDialog";
import { TimeField } from "../TimeField";
import { useToast } from "../Toasts";
import { Tooltip } from "../Tooltip";
import { NumberField, Row, Section } from "./Inspector";

const ACCEPT = ".mp3,.wav,.m4a,.aac,.ogg,.opus,.flac,audio/*";
const issueOf = (err: unknown): ApiIssue => (err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error", params: {} });
const smallBtn = "inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-xs hover:bg-white/5 disabled:opacity-50";

/** Background music: a shared library of tracks + this clip's music settings (EDL `music`). */
export function MusicTab({
  edl,
  clipTime,
  duration,
  onMusic,
}: {
  edl: Edl;
  clipTime: number;
  duration: number;
  onMusic: (m: EdlMusic, mode?: "record" | "live", key?: string) => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const m = edl.music ?? DEFAULT_MUSIC;
  const [tracks, setTracks] = useState<MusicTrack[] | null>(null);
  const [loadError, setLoadError] = useState<ApiIssue | null>(null);
  const [uploading, setUploading] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<MusicTrack | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const preview = useRef<HTMLAudioElement>(null);

  const load = useCallback(() => {
    setLoadError(null);
    listMusic().then(setTracks, (err) => setLoadError(issueOf(err)));
  }, []);
  useEffect(load, [load]);
  useEffect(() => () => preview.current?.pause(), []);

  const use = (track: MusicTrack) =>
    onMusic({ ...m, enabled: true, track_id: track.id, name: track.name, duration: track.duration, offset: 0 });

  const onFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const track = await uploadMusic(file);
      setTracks((prev) => [...(prev ?? []).filter((x) => x.id !== track.id), track].sort((a, b) => a.name.localeCompare(b.name)));
      use(track);
      toast.show(t("pages.editor.music.added", { name: track.name }), "success");
    } catch (err) {
      toast.show(translateIssue(t, issueOf(err)), "error");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const togglePreview = (track: MusicTrack) => {
    const el = preview.current;
    if (!el) return;
    if (playing === track.id) {
      el.pause();
      setPlaying(null);
      return;
    }
    el.src = apiUrl(track.url);
    el.currentTime = 0;
    void el.play().then(
      () => setPlaying(track.id),
      () => setPlaying(null),
    );
  };

  const confirmDelete = async () => {
    const track = toDelete;
    setToDelete(null);
    if (!track) return;
    try {
      await deleteMusic(track.id);
      setTracks((prev) => (prev ?? []).filter((x) => x.id !== track.id));
      if (playing === track.id) {
        preview.current?.pause();
        setPlaying(null);
      }
      if (m.track_id === track.id) onMusic({ ...m, enabled: false, track_id: null, name: "", duration: null });
    } catch (err) {
      toast.show(translateIssue(t, issueOf(err)), "error");
    }
  };

  const db = (v: number) => t("pages.editor.db", { value: v > 0 ? `+${v}` : v });
  const selected = m.track_id ? (tracks ?? []).find((x) => x.id === m.track_id) : undefined;
  const missing = !!m.track_id && tracks !== null && !selected;
  const trackLen = m.duration ?? selected?.duration ?? 0;

  return (
    <div data-testid="music-tab">
      <Section
        title={t("pages.editor.music.library")}
        actions={
          <Tooltip label={t("pages.editor.music.add_hint")}>
            <button type="button" className={smallBtn} disabled={uploading} onClick={() => fileInput.current?.click()} data-testid="music-upload">
              {uploading ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
              {uploading ? t("pages.editor.music.uploading") : t("pages.editor.music.add")}
            </button>
          </Tooltip>
        }
      >
        <input ref={fileInput} type="file" accept={ACCEPT} hidden onChange={(e) => void onFiles(e.target.files)} data-testid="music-file" />
        {loadError ? (
          <div className="text-xs text-danger">
            {translateIssue(t, loadError)}{" "}
            <button type="button" className="underline" onClick={load}>
              {t("common.retry")}
            </button>
          </div>
        ) : tracks === null ? (
          <div className="space-y-1.5">
            {[0, 1].map((i) => (
              <div key={i} className="h-9 animate-pulse rounded-md bg-white/5" />
            ))}
          </div>
        ) : tracks.length === 0 ? (
          <p className="text-xs text-muted">{t("pages.editor.music.empty")}</p>
        ) : (
          <ul className="space-y-1" data-testid="music-list">
            {tracks.map((track) => {
              const active = track.id === m.track_id;
              return (
                <li
                  key={track.id}
                  className={`flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm ${active ? "border-accent/70 bg-accent/10" : "border-line hover:border-line-strong"}`}
                >
                  <Tooltip label={playing === track.id ? t("pages.editor.music.stop_preview") : t("pages.editor.music.play_preview")}>
                    <button
                      type="button"
                      onClick={() => togglePreview(track)}
                      aria-label={playing === track.id ? t("pages.editor.music.stop_preview") : t("pages.editor.music.play_preview")}
                      className="grid size-6 shrink-0 place-items-center rounded-full border border-line hover:bg-white/10"
                    >
                      {playing === track.id ? <Pause className="size-3" /> : <Play className="size-3" />}
                    </button>
                  </Tooltip>
                  <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => use(track)} title={track.name} data-testid="music-track">
                    {track.name}
                  </button>
                  <span className="shrink-0 font-mono text-xs text-muted">{formatDuration(track.duration)}</span>
                  {active ? (
                    <span className="shrink-0 text-xs text-accent">{t("pages.editor.music.in_use")}</span>
                  ) : (
                    <button type="button" className="shrink-0 text-xs text-muted hover:text-fg" onClick={() => use(track)}>
                      {t("pages.editor.music.use")}
                    </button>
                  )}
                  <Tooltip label={t("pages.editor.music.delete")}>
                    <button
                      type="button"
                      aria-label={t("pages.editor.music.delete")}
                      onClick={() => setToDelete(track)}
                      className="shrink-0 rounded p-1 text-muted hover:bg-white/10 hover:text-danger"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </Tooltip>
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-2 text-xs text-subtle">{t("pages.editor.music.rights")}</p>
        <audio ref={preview} onEnded={() => setPlaying(null)} hidden />
      </Section>

      <Section title={t("pages.editor.music.clip")}>
        {!m.track_id ? (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Music2 className="size-4 shrink-0" />
            {t("pages.editor.music.none")}
          </div>
        ) : (
          <>
            <label className="mb-3 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={m.enabled}
                onChange={(e) => onMusic({ ...m, enabled: e.target.checked })}
                className="accent-[var(--color-accent)]"
                data-testid="music-enabled"
              />
              <span className="min-w-0 truncate">{t("pages.editor.music.enabled", { name: m.name || selected?.name || "" })}</span>
            </label>
            {missing && <p className="mb-2 text-xs text-warning">{t("pages.editor.music.missing")}</p>}
            <Row label={t("pages.editor.volume")} hint={t("pages.editor.music.volume_hint")}>
              <span className="flex items-center gap-2">
                <input
                  type="range"
                  min={-40}
                  max={6}
                  step={1}
                  value={m.volume_db}
                  aria-label={t("pages.editor.volume")}
                  data-testid="music-volume"
                  onChange={(e) => onMusic({ ...m, volume_db: Number(e.target.value) }, "record", "music_volume")}
                  className="min-w-0 flex-1 accent-[var(--color-accent)]"
                />
                <span className="w-14 text-right font-mono text-xs">{db(m.volume_db)}</span>
              </span>
            </Row>
            <Row label={t("pages.editor.music.start")} hint={t("pages.editor.music.start_hint")}>
              <span className="flex items-center gap-1.5">
                <TimeField value={m.start} min={0} max={Math.max(0, duration - 0.1)} label={t("pages.editor.music.start")} onChange={(v) => onMusic({ ...m, start: v })} className="min-w-0 flex-1" testId="music-start" />
                <Tooltip label={t("pages.editor.music.at_playhead")}>
                  <button
                    type="button"
                    className={smallBtn}
                    aria-label={t("pages.editor.music.at_playhead")}
                    onClick={() => onMusic({ ...m, start: Math.max(0, Math.min(duration - 0.1, clipTime)) })}
                  >
                    <Crosshair className="size-3.5" />
                  </button>
                </Tooltip>
              </span>
            </Row>
            <Row label={t("pages.editor.music.offset")} hint={t("pages.editor.music.offset_hint")}>
              <TimeField value={m.offset} min={0} max={Math.max(0, trackLen - 0.1)} label={t("pages.editor.music.offset")} onChange={(v) => onMusic({ ...m, offset: v })} testId="music-offset" />
            </Row>
            <Row label={t("pages.editor.music.fade_in")}>
              <NumberField value={m.fade_in} min={0} max={10} step={0.1} digits={1} label={t("pages.editor.music.fade_in")} onCommit={(v) => onMusic({ ...m, fade_in: v })} testId="music-fade-in" />
            </Row>
            <Row label={t("pages.editor.music.fade_out")}>
              <NumberField value={m.fade_out} min={0} max={10} step={0.1} digits={1} label={t("pages.editor.music.fade_out")} onCommit={(v) => onMusic({ ...m, fade_out: v })} testId="music-fade-out" />
            </Row>
            <label className="mb-2 flex items-center gap-2 text-sm" title={t("pages.editor.music.loop_hint")}>
              <input type="checkbox" checked={m.loop} onChange={(e) => onMusic({ ...m, loop: e.target.checked })} className="accent-[var(--color-accent)]" data-testid="music-loop" />
              {t("pages.editor.music.loop")}
            </label>
            <Row label={t("pages.editor.music.duck")} hint={t("pages.editor.music.duck_hint")}>
              <span className="flex items-center gap-2">
                <input
                  type="range"
                  min={-40}
                  max={0}
                  step={1}
                  value={m.duck_db}
                  aria-label={t("pages.editor.music.duck")}
                  onChange={(e) => onMusic({ ...m, duck_db: Number(e.target.value) }, "record", "music_duck")}
                  className="min-w-0 flex-1 accent-[var(--color-accent)]"
                />
                <span className="w-14 text-right font-mono text-xs">{db(m.duck_db)}</span>
              </span>
            </Row>
            <p className="text-xs text-muted">{edl.voiceover.audio_path && edl.voiceover.enabled ? t("pages.editor.music.duck_active") : t("pages.editor.music.duck_note")}</p>
          </>
        )}
      </Section>

      <ConfirmDialog
        open={toDelete !== null}
        danger
        title={t("pages.editor.music.delete_title")}
        confirmLabel={t("pages.editor.music.delete")}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      >
        <p>{t("pages.editor.music.delete_body", { name: toDelete?.name ?? "" })}</p>
      </ConfirmDialog>
    </div>
  );
}
