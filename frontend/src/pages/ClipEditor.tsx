import {
  ArrowLeft,
  Clapperboard,
  Pause,
  Play,
  Redo2,
  Scissors,
  ShieldAlert,
  SkipBack,
  StepBack,
  StepForward,
  Trash2,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { EditorTimeline } from "../components/editor/EditorTimeline";
import { AudioTab, FramingTab, INSPECTOR_TABS, PlaceholderTab, type InspectorTab } from "../components/editor/Inspector";
import { PreviewCanvas } from "../components/editor/PreviewCanvas";
import { SourceView, type Phase, type SplitPanel } from "../components/editor/SourceView";
import { EmptyState, ErrorState, Skeleton } from "../components/states";
import { useToast } from "../components/Toasts";
import { Tooltip } from "../components/Tooltip";
import {
  ApiError,
  apiUrl,
  getClip,
  getWaveform,
  listClips,
  openMomentClip,
  patchClip,
  reframeClip,
  type ApiIssue,
  type Clip,
  type ClipSummary,
  type Edl,
  type EdlSection,
  type Waveform,
} from "../lib/api";
import {
  clipDuration,
  defaultSplit,
  interpolate,
  playableTime,
  refitSplit,
  removeSegment,
  sortKeyframes,
  splitSegment,
  toClipTime,
  toSourceTime,
  upsertKeyframe,
  type CropMode,
  type Region,
} from "../lib/edl";
import { translateIssue } from "../lib/errors";
import { formatDuration, formatTimecode } from "../lib/format";
import { frameStep, HISTORY_LIMIT } from "../lib/moments";
import { useSaveStatus } from "../state/save";
import { useSources } from "../state/sources";

const SECTIONS: EdlSection[] = ["segments", "crop", "captions", "hook_text", "progress_bar", "voiceover", "audio", "platform", "output"];
const LAST_CLIP_KEY = "shortforge.editor.lastClip";
const SAFE_KEY = "shortforge.editor.safeZones";
const TIME_KEY = (id: string) => `shortforge.editor.${id}`;
const COALESCE_MS = 800;

function storageGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function storageSet(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}
function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Clip Editor — checkpoint 2. /editor?clip=<id>, /editor?moment=<id> (opens or creates the clip). */
export default function ClipEditor() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const clipParam = params.get("clip");
  const momentParam = params.get("moment");
  const [openError, setOpenError] = useState<ApiIssue | null>(null);

  useEffect(() => {
    if (!momentParam) return;
    let cancelled = false;
    setOpenError(null);
    openMomentClip(Number(momentParam))
      .then((clip) => !cancelled && setParams({ clip: clip.id }, { replace: true }))
      .catch((err) => !cancelled && setOpenError(err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error", params: {} }));
    return () => {
      cancelled = true;
    };
  }, [momentParam, setParams]);

  if (momentParam) {
    if (openError) {
      return (
        <div className="p-6">
          <ErrorState message={translateIssue(t, openError)} onRetry={() => navigate(0)} />
        </div>
      );
    }
    return (
      <div className="p-6">
        <Skeleton rows={6} />
      </div>
    );
  }
  if (!clipParam) return <ClipPicker onOpen={(id) => setParams({ clip: id })} />;
  return <EditorWorkspace key={clipParam} clipId={clipParam} />;
}

/** No clip in the URL: reopen the last one, or list the clips of the current source. */
function ClipPicker({ onOpen }: { onOpen: (id: string) => void }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { sources, loaded, currentSource, setCurrentSourceId } = useSources();
  const [clips, setClips] = useState<ClipSummary[] | null>(null);
  const [error, setError] = useState<ApiIssue | null>(null);
  const [attempt, setAttempt] = useState(0);
  const tried = useRef(false);
  const source = currentSource ?? sources.find((s) => s.moments.approved > 0) ?? sources[0] ?? null;

  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    setError(null);
    listClips(source.id)
      .then((list) => {
        if (cancelled) return;
        setClips(list);
        const last = storageGet(LAST_CLIP_KEY);
        if (!tried.current && last && list.some((c) => c.id === last)) onOpen(last);
        tried.current = true;
      })
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error", params: {} }));
    return () => {
      cancelled = true;
    };
  }, [source, attempt, onOpen]);

  if (loaded && !source) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <EmptyState icon={Clapperboard} message={t("pages.editor.empty")} action={{ label: t("pages.editor.action"), onClick: () => navigate("/moments") }} />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-4xl p-6" data-testid="clip-picker">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{t("pages.editor.title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("pages.editor.pick_subtitle")}</p>
        </div>
        {sources.length > 1 && source && (
          <select
            aria-label={t("pages.moments.source")}
            value={source.id}
            onChange={(e) => {
              setClips(null);
              setCurrentSourceId(e.target.value);
            }}
            className="max-w-72 min-w-0 rounded-lg border border-line bg-panel px-3 py-2 text-sm"
          >
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        )}
      </div>
      {error ? (
        <ErrorState message={translateIssue(t, error)} onRetry={() => setAttempt((n) => n + 1)} />
      ) : clips === null ? (
        <Skeleton rows={4} />
      ) : clips.length === 0 ? (
        <EmptyState icon={Clapperboard} message={t("pages.editor.no_clips")} action={{ label: t("pages.editor.action"), onClick: () => navigate("/moments") }} />
      ) : (
        <ul className="space-y-2">
          {clips.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                data-testid="clip-item"
                onClick={() => onOpen(c.id)}
                className="flex w-full items-center gap-4 rounded-lg border border-line bg-panel px-4 py-3 text-left hover:border-muted"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium" data-content>{c.title || t("pages.moments.untitled")}</span>
                  <span className="mt-0.5 block font-mono text-xs text-muted">
                    {formatTimecode(c.moment?.start ?? 0)} · {formatDuration(c.duration)}
                  </span>
                </span>
                {c.crop_mode && <span className="shrink-0 rounded-full border border-line px-2 py-0.5 text-xs text-muted">{t(`pages.editor.mode_name.${c.crop_mode}`)}</span>}
                <span className="shrink-0 text-xs text-muted">{t(`clip_status.${c.status}`)}</span>
                <span className="shrink-0 text-xs text-muted">{c.updated_at ? new Date(c.updated_at).toLocaleString(i18n.language) : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface History {
  past: Edl[];
  future: Edl[];
  lastKey: string | null;
  lastAt: number;
}

function EditorWorkspace({ clipId }: { clipId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  const { track } = useSaveStatus();
  const { sources, setCurrentSourceId, refresh: refreshSources } = useSources();

  const [clip, setClip] = useState<Clip | null>(null);
  const [edl, setEdlState] = useState<Edl | null>(null);
  const [loadError, setLoadError] = useState<ApiIssue | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [waveform, setWaveform] = useState<Waveform | null>(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState<InspectorTab>("framing");
  const [selectedSegment, setSelectedSegment] = useState<number | null>(null);
  const [selectedKeyframe, setSelectedKeyframe] = useState<number | null>(null);
  const [drawTarget, setDrawTarget] = useState<SplitPanel | null>(null);
  const [safeZones, setSafeZones] = useState(() => storageGet(SAFE_KEY) !== "0");
  const [confirmRecompute, setConfirmRecompute] = useState(false);
  const [, rerender] = useReducer((n: number) => n + 1, 0);

  const video = useRef<HTMLVideoElement>(null);
  const edlRef = useRef<Edl | null>(null);
  const saved = useRef<Edl | null>(null);
  const history = useRef<History>({ past: [], future: [], lastKey: null, lastAt: 0 });
  const gestureBase = useRef<Edl | null>(null);
  const wheelTimer = useRef<number | undefined>(undefined);
  const saveTimer = useRef<number | undefined>(undefined);
  const wasBusy = useRef(false);

  const source = sources.find((s) => s.id === clip?.source_id) ?? null;
  const srcW = source?.width || 1920;
  const srcH = source?.height || 1080;
  const duration = edl ? clipDuration(edl.segments) : 0;

  const fail = useCallback(
    (err: unknown) => {
      const issue = err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error" as const, params: {} };
      toast.show(translateIssue(t, issue), "error");
    },
    [toast, t],
  );

  // ------------------------------------------------------------------ load
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    getClip(clipId)
      .then((c) => {
        if (cancelled) return;
        setClip(c);
        edlRef.current = c.edl;
        saved.current = c.edl;
        setEdlState(c.edl);
        wasBusy.current = c.busy;
        setCurrentSourceId(c.source_id);
        storageSet(LAST_CLIP_KEY, c.id);
        getWaveform(c.source_id).then((w) => !cancelled && setWaveform(w), () => undefined);
      })
      .catch((err) => !cancelled && setLoadError(err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error", params: {} }));
    return () => {
      cancelled = true;
    };
  }, [clipId, attempt, setCurrentSourceId]);

  // ------------------------------------------------------------------ save (500 ms debounce)
  const flush = useCallback(async () => {
    window.clearTimeout(saveTimer.current);
    const cur = edlRef.current;
    const base = saved.current;
    if (!cur || !base) return;
    const patch: Partial<Pick<Edl, EdlSection>> = {};
    for (const key of SECTIONS) if (!same(cur[key], base[key])) (patch as Record<string, unknown>)[key] = cur[key];
    if (Object.keys(patch).length === 0) return;
    saved.current = { ...base, ...patch };
    try {
      const res = await track(patchClip(clipId, { edl: patch }));
      setClip(res);
    } catch (err) {
      saved.current = base; // retried with the next edit
      fail(err);
    }
  }, [clipId, track, fail]);

  const scheduleSave = useCallback(() => {
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void flush(), 500);
  }, [flush]);

  useEffect(() => () => void flush(), [flush]); // save when leaving

  // ------------------------------------------------------------------ edits + history
  const pushPast = (prev: Edl) => {
    const h = history.current;
    h.past = [...h.past, prev].slice(-HISTORY_LIMIT);
    h.future = [];
  };

  /** Apply an edit. "record" adds an undo step (merged with the previous one for the same
   *  coalesce key within 800 ms, e.g. slider drags); "live" is part of a running gesture. */
  const apply = useCallback(
    (next: Edl, mode: "record" | "live" = "record", coalesceKey?: string) => {
      const prev = edlRef.current;
      if (!prev || same(prev, next)) return;
      if (mode === "record" && !gestureBase.current) {
        const h = history.current;
        const now = Date.now();
        if (!(coalesceKey && h.lastKey === coalesceKey && now - h.lastAt < COALESCE_MS)) pushPast(prev);
        h.lastKey = coalesceKey ?? null;
        h.lastAt = now;
      }
      edlRef.current = next;
      setEdlState(next);
      scheduleSave();
      rerender();
    },
    [scheduleSave],
  );

  const beginGesture = useCallback(() => {
    if (!gestureBase.current) gestureBase.current = edlRef.current;
  }, []);
  const endGesture = useCallback(() => {
    const base = gestureBase.current;
    gestureBase.current = null;
    if (base && !same(base, edlRef.current)) {
      pushPast(base);
      history.current.lastKey = null;
      rerender();
    }
  }, []);

  const undo = useCallback(() => {
    const h = history.current;
    const cur = edlRef.current;
    const prev = h.past[h.past.length - 1];
    if (!prev || !cur) return;
    h.past = h.past.slice(0, -1);
    h.future = [cur, ...h.future];
    h.lastKey = null;
    edlRef.current = prev;
    setEdlState(prev);
    scheduleSave();
    rerender();
  }, [scheduleSave]);

  const redo = useCallback(() => {
    const h = history.current;
    const cur = edlRef.current;
    const [next, ...rest] = h.future;
    if (!next || !cur) return;
    h.future = rest;
    h.past = [...h.past, cur].slice(-HISTORY_LIMIT);
    h.lastKey = null;
    edlRef.current = next;
    setEdlState(next);
    scheduleSave();
    rerender();
  }, [scheduleSave]);

  const setSection = useCallback(
    <K extends EdlSection>(key: K, value: Edl[K], mode: "record" | "live" = "record", coalesceKey?: string) => {
      const cur = edlRef.current;
      if (cur) apply({ ...cur, [key]: value }, mode, coalesceKey);
    },
    [apply],
  );

  // ------------------------------------------------------------------ background work (reframe job)
  useEffect(() => {
    if (!clip?.busy) return;
    const timer = window.setInterval(async () => {
      try {
        const fresh = await getClip(clipId);
        setClip(fresh);
        if (!fresh.busy && wasBusy.current) {
          wasBusy.current = false;
          const local = edlRef.current;
          const base = saved.current;
          // adopt the computed framing unless the user changed it locally in the meantime
          if (local && base && same(local.crop, base.crop) && !same(fresh.edl.crop, base.crop)) {
            pushPast(local);
            const next = { ...local, crop: fresh.edl.crop };
            saved.current = { ...base, crop: fresh.edl.crop };
            edlRef.current = next;
            setEdlState(next);
            rerender();
          }
          if (fresh.work.reframe.state === "done" && fresh.work.reframe.info?.applied !== false) toast.show(t("pages.editor.reframe_done"), "success");
          if (fresh.work.reframe.state === "error" || fresh.work.prepare.state === "error") toast.show(t("pages.editor.reframe_failed"), "error");
        }
      } catch {
        /* next poll */
      }
    }, 1000);
    wasBusy.current = true;
    return () => window.clearInterval(timer);
  }, [clip?.busy, clipId, toast, t]);

  // ------------------------------------------------------------------ playback (skips gaps between segments)
  const seek = useCallback((clipT: number) => {
    const cur = edlRef.current;
    const v = video.current;
    if (!cur) return;
    const total = clipDuration(cur.segments);
    const tt = Math.min(Math.max(0, clipT), Math.max(0, total - 0.001));
    setTime(tt);
    if (v) v.currentTime = toSourceTime(cur.segments, tt);
  }, []);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const v = video.current;
      const cur = edlRef.current;
      if (!v || !cur || v.readyState < 1) return;
      if (!v.paused) {
        const p = playableTime(cur.segments, v.currentTime);
        if (p === null) {
          v.pause();
          v.currentTime = cur.segments[cur.segments.length - 1].end - 0.001;
        } else if (Math.abs(p - v.currentTime) > 0.03) {
          v.currentTime = p;
        }
      }
      const ct = toClipTime(cur.segments, v.currentTime);
      if (ct !== null) setTime((prev) => (Math.abs(prev - ct) > 1 / 60 ? ct : prev));
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // restore the playhead, remember it
  const onVideoReady = useCallback(() => {
    const stored = Number(storageGet(TIME_KEY(clipId)) ?? 0);
    seek(Number.isFinite(stored) ? stored : 0);
  }, [clipId, seek]);
  useEffect(() => {
    const handle = window.setTimeout(() => storageSet(TIME_KEY(clipId), String(Math.round(time * 100) / 100)), 300);
    return () => window.clearTimeout(handle);
  }, [time, clipId]);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("loadedmetadata", onVideoReady);
    if (v.readyState >= 1) onVideoReady();
    return () => {
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("loadedmetadata", onVideoReady);
    };
  }, [onVideoReady, source?.proxy_url, edl === null]); // eslint-disable-line react-hooks/exhaustive-deps

  // preview volume follows the audio tab (the render applies gain + loudness normalization)
  useEffect(() => {
    const v = video.current;
    if (v && edl) v.volume = Math.min(1, Math.pow(10, edl.audio.volume_db / 20));
  }, [edl?.audio.volume_db]); // eslint-disable-line react-hooks/exhaustive-deps

  const togglePlay = useCallback(() => {
    const v = video.current;
    const cur = edlRef.current;
    if (!v || !cur) return;
    if (v.paused) {
      const ct = toClipTime(cur.segments, v.currentTime);
      if (ct === null || ct >= clipDuration(cur.segments) - 0.05) seek(0);
      void v.play().catch(() => undefined);
    } else v.pause();
  }, [seek]);

  // ------------------------------------------------------------------ framing actions
  const cropEdit = useCallback(
    (center: { x: number; y: number; zoom: number }, mode: "record" | "live") => {
      const cur = edlRef.current;
      if (!cur) return;
      const crop = cur.crop;
      const keyframes = upsertKeyframe(crop.keyframes, { t: time, ...center });
      const nextMode: CropMode = crop.mode === "auto" || crop.mode === "cursor" ? "manual" : crop.mode;
      apply({ ...cur, crop: { ...crop, mode: nextMode, keyframes } }, mode);
      setSelectedKeyframe(sortKeyframes(keyframes).findIndex((k) => Math.abs(k.t - time) < 1 / 60));
    },
    [apply, time],
  );

  const onCrop = useCallback(
    (center: { x: number; y: number; zoom: number }, phase: Phase) => {
      if (phase === "start") beginGesture();
      else if (phase === "move") cropEdit(center, "live");
      else endGesture();
    },
    [beginGesture, endGesture, cropEdit],
  );

  const onZoomWheel = useCallback(
    (zoom: number) => {
      const cur = edlRef.current;
      if (!cur) return;
      beginGesture();
      const [x, y] = interpolate(cur.crop.keyframes, time, cur.crop.easing);
      cropEdit({ x, y, zoom }, "live");
      window.clearTimeout(wheelTimer.current);
      wheelTimer.current = window.setTimeout(endGesture, 400);
    },
    [beginGesture, endGesture, cropEdit, time],
  );

  const addKeyframe = useCallback(() => {
    const cur = edlRef.current;
    if (!cur || cur.crop.mode === "split") return;
    const [x, y, zoom] = interpolate(cur.crop.keyframes, time, cur.crop.easing);
    cropEdit({ x, y, zoom }, "record");
  }, [cropEdit, time]);

  const setMode = useCallback(
    (mode: CropMode) => {
      const cur = edlRef.current;
      if (!cur || cur.crop.mode === mode) return;
      const split = mode === "split" && !cur.crop.split ? defaultSplit(srcW, srcH, cur.output.w, cur.output.h) : cur.crop.split;
      setDrawTarget(null);
      apply({ ...cur, crop: { ...cur.crop, mode, split } });
    },
    [apply, srcW, srcH],
  );

  const onSplit = useCallback(
    (panel: SplitPanel, region: Region, phase: Phase) => {
      const cur = edlRef.current;
      if (!cur?.crop.split) return;
      if (phase === "start") return beginGesture();
      if (phase === "end") {
        endGesture();
        setDrawTarget(null);
        return;
      }
      beginGesture();
      apply({ ...cur, crop: { ...cur.crop, split: { ...cur.crop.split, [panel]: region } } }, "live");
    },
    [apply, beginGesture, endGesture],
  );

  const onSplitRatio = useCallback(
    (ratio: number, phase: "move" | "end") => {
      const cur = edlRef.current;
      if (!cur?.crop.split) return;
      beginGesture();
      apply({ ...cur, crop: { ...cur.crop, split: refitSplit(cur.crop.split, ratio, srcW, srcH, cur.output.w, cur.output.h) } }, "live");
      if (phase === "end") endGesture();
    },
    [apply, beginGesture, endGesture, srcW, srcH],
  );

  const recompute = useCallback(async () => {
    setConfirmRecompute(false);
    const cur = edlRef.current;
    if (!cur) return;
    try {
      await flush();
      const res = await track(reframeClip(clipId, cur.crop.mode === "manual" ? undefined : cur.crop.mode));
      setClip(res);
      void refreshSources();
    } catch (err) {
      fail(err);
    }
  }, [clipId, flush, track, fail, refreshSources]);

  // ------------------------------------------------------------------ segments
  const splitAtPlayhead = useCallback(() => {
    const cur = edlRef.current;
    if (!cur) return;
    const segments = splitSegment(cur.segments, time);
    if (segments.length === cur.segments.length) return;
    apply({ ...cur, segments });
  }, [apply, time]);

  const removeSelectedSegment = useCallback(() => {
    const cur = edlRef.current;
    if (!cur || selectedSegment === null) return;
    if (cur.segments.length <= 1) {
      toast.show(t("pages.editor.last_segment"), "info");
      return;
    }
    const res = removeSegment(cur.segments, cur.crop.keyframes, selectedSegment);
    apply({ ...cur, segments: res.segments, crop: { ...cur.crop, keyframes: res.keyframes } });
    setSelectedSegment(null);
    setSelectedKeyframe(null);
  }, [apply, selectedSegment, toast, t]);

  // ------------------------------------------------------------------ keyboard
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (isTyping(e.target) || e.altKey) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (mod && k === "z") {
      e.preventDefault();
      if (e.shiftKey) redo();
      else undo();
      return;
    }
    if (mod && k === "y") {
      e.preventDefault();
      redo();
      return;
    }
    if (mod) return;
    const fps = source?.fps;
    if (e.key === " ") {
      e.preventDefault();
      togglePlay();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const delta = e.shiftKey ? 1 : frameStep(fps);
      seek(time + (e.key === "ArrowLeft" ? -delta : delta));
    } else if (e.key === "Home") {
      e.preventDefault();
      seek(0);
    } else if (e.key === "End") {
      e.preventDefault();
      seek(duration);
    } else if (k === "s") {
      e.preventDefault();
      splitAtPlayhead();
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeSelectedSegment();
    } else if (k === "f") {
      e.preventDefault();
      addKeyframe();
    } else if (e.key === "Escape") {
      setDrawTarget(null);
      setSelectedSegment(null);
    }
  };
  useEffect(() => {
    const handler = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const backToMoments = useCallback(async () => {
    await flush();
    if (clip) setCurrentSourceId(clip.source_id);
    navigate("/moments");
  }, [flush, clip, setCurrentSourceId, navigate]);

  const proxySrc = useMemo(() => (source?.proxy_url ? apiUrl(source.proxy_url) : null), [source?.proxy_url]);

  // ------------------------------------------------------------------ render
  if (loadError) {
    return (
      <div className="p-6">
        <ErrorState message={translateIssue(t, loadError)} onRetry={() => setAttempt((n) => n + 1)} />
      </div>
    );
  }
  if (!clip || !edl) {
    return (
      <div className="p-6" data-testid="editor-loading">
        <Skeleton rows={8} />
      </div>
    );
  }

  const canUndo = history.current.past.length > 0;
  const canRedo = history.current.future.length > 0;
  const iconBtn = "flex size-8 items-center justify-center rounded-md text-muted hover:bg-white/5 hover:text-fg disabled:opacity-30 disabled:hover:bg-transparent";

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="clip-editor" data-clip-id={clip.id}>
      {/* toolbar */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        <Tooltip label={t("pages.editor.back_hint")} side="bottom">
          <button type="button" onClick={() => void backToMoments()} data-testid="back-to-moments" className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-muted hover:bg-white/5 hover:text-fg">
            <ArrowLeft className="size-4" />
            {t("pages.editor.back")}
          </button>
        </Tooltip>
        <div className="mx-2 h-5 w-px bg-line" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" data-testid="clip-title" data-content title={clip.title}>
            {clip.title || t("pages.moments.untitled")}
          </div>
        </div>
        <Tooltip label={t("pages.moments.undo")} shortcut="Ctrl+Z" side="bottom">
          <button type="button" aria-label={t("pages.moments.undo")} disabled={!canUndo} onClick={undo} className={iconBtn} data-testid="undo">
            <Undo2 className="size-4" />
          </button>
        </Tooltip>
        <Tooltip label={t("pages.moments.redo")} shortcut="Ctrl+Shift+Z" side="bottom">
          <button type="button" aria-label={t("pages.moments.redo")} disabled={!canRedo} onClick={redo} className={iconBtn} data-testid="redo">
            <Redo2 className="size-4" />
          </button>
        </Tooltip>
        <Tooltip label={t("pages.editor.safe_zones_hint")} side="bottom">
          <button
            type="button"
            aria-pressed={safeZones}
            data-testid="toggle-safe-zones"
            onClick={() => {
              setSafeZones((v) => {
                storageSet(SAFE_KEY, v ? "0" : "1");
                return !v;
              });
            }}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs ${safeZones ? "border-warning/60 bg-warning/10 text-warning" : "border-line text-muted hover:bg-white/5"}`}
          >
            <ShieldAlert className="size-3.5" />
            {t("pages.editor.safe_zones")}
          </button>
        </Tooltip>
      </div>

      {/* viewers + inspector */}
      <div className="grid min-h-0 flex-1 grid-cols-[clamp(240px,22vw,360px)_minmax(0,1fr)_380px]">
        <div className="flex min-h-0 flex-col items-center justify-center gap-2 border-r border-line p-3">
          <div className="flex min-h-0 w-full flex-1 items-center justify-center">
            <PreviewCanvas videoRef={video} edl={edl} showSafeZones={safeZones} />
          </div>
          <span className="truncate text-[11px] text-muted">
            {t(`platform.${edl.platform}`)} · {edl.output.w}×{edl.output.h}
          </span>
        </div>

        <div className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto p-3">
          <SourceView
            ref={video}
            src={proxySrc}
            edl={edl}
            clipTime={time}
            srcW={srcW}
            srcH={srcH}
            drawTarget={drawTarget}
            onTogglePlay={togglePlay}
            onCrop={onCrop}
            onZoomWheel={onZoomWheel}
            onSplit={onSplit}
          />
          <div className="flex flex-wrap items-center gap-1">
            <Tooltip label={t("pages.editor.to_start")} shortcut="Home" side="top">
              <button type="button" aria-label={t("pages.editor.to_start")} onClick={() => seek(0)} className={iconBtn}>
                <SkipBack className="size-4" />
              </button>
            </Tooltip>
            <Tooltip label={t("pages.moments.prev_frame")} shortcut="←" side="top">
              <button type="button" aria-label={t("pages.moments.prev_frame")} onClick={() => seek(time - frameStep(source?.fps))} className={iconBtn}>
                <StepBack className="size-4" />
              </button>
            </Tooltip>
            <Tooltip label={playing ? t("pages.moments.pause") : t("pages.moments.play")} shortcut="Space" side="top">
              <button
                type="button"
                aria-label={playing ? t("pages.moments.pause") : t("pages.moments.play")}
                onClick={togglePlay}
                data-testid="editor-play"
                className="flex size-9 items-center justify-center rounded-full bg-accent text-white hover:bg-accent/90"
              >
                {playing ? <Pause className="size-4" /> : <Play className="size-4 translate-x-px" />}
              </button>
            </Tooltip>
            <Tooltip label={t("pages.moments.next_frame")} shortcut="→" side="top">
              <button type="button" aria-label={t("pages.moments.next_frame")} onClick={() => seek(time + frameStep(source?.fps))} className={iconBtn}>
                <StepForward className="size-4" />
              </button>
            </Tooltip>
            <span className="ml-2 font-mono text-xs" data-testid="editor-time">
              {formatTimecode(time)} <span className="text-muted">/ {formatTimecode(duration)}</span>
            </span>
            <span className="flex-1" />
            <Tooltip label={t("pages.editor.split_segment")} shortcut="S" side="top">
              <button type="button" onClick={splitAtPlayhead} data-testid="split-segment" className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs hover:bg-white/5">
                <Scissors className="size-3.5" />
                {t("pages.editor.split_segment_short")}
              </button>
            </Tooltip>
            <Tooltip label={t("pages.editor.remove_segment")} shortcut="Delete" side="top">
              <button
                type="button"
                onClick={removeSelectedSegment}
                disabled={selectedSegment === null || edl.segments.length <= 1}
                data-testid="remove-segment"
                className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs hover:bg-white/5 disabled:opacity-30"
              >
                <Trash2 className="size-3.5" />
                {t("pages.editor.remove_segment_short")}
              </button>
            </Tooltip>
          </div>
          <p className="text-xs text-muted">{drawTarget ? t(`pages.editor.drawing_${drawTarget}`) : t(`pages.editor.view_hint.${edl.crop.mode === "split" ? "split" : edl.crop.fit === "blur" ? "blur" : "crop"}`)}</p>
        </div>

        <aside className="flex min-h-0 flex-col border-l border-line bg-panel">
          <div className="flex shrink-0 overflow-x-auto border-b border-line" role="tablist" aria-label={t("pages.editor.inspector")}>
            {INSPECTOR_TABS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                data-testid={`tab-${key}`}
                onClick={() => setTab(key)}
                className={`min-w-0 flex-1 truncate border-b-2 px-1.5 py-2.5 text-xs whitespace-nowrap ${tab === key ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}
              >
                {t(`pages.editor.tab.${key}`)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === "framing" ? (
              <FramingTab
                clip={clip}
                edl={edl}
                clipTime={time}
                duration={duration}
                selectedKeyframe={selectedKeyframe}
                drawTarget={drawTarget}
                onCrop={(crop) => setSection("crop", crop)}
                onAddKeyframe={addKeyframe}
                onSelectKeyframe={setSelectedKeyframe}
                onSeek={seek}
                onSetMode={setMode}
                onRecompute={() => setConfirmRecompute(true)}
                onSplitRatio={onSplitRatio}
                onDraw={setDrawTarget}
              />
            ) : tab === "audio" ? (
              <AudioTab edl={edl} onAudio={(a) => setSection("audio", a, "record", "audio")} onVoiceover={(v) => setSection("voiceover", v, "record", "duck")} />
            ) : (
              <PlaceholderTab tab={tab} />
            )}
          </div>
        </aside>
      </div>

      <div className="h-[220px] shrink-0">
        <EditorTimeline
          edl={edl}
          time={time}
          waveform={waveform}
          selectedSegment={selectedSegment}
          selectedKeyframe={selectedKeyframe}
          onSeek={seek}
          onSelectSegment={setSelectedSegment}
          onSelectKeyframe={setSelectedKeyframe}
        />
      </div>

      <ConfirmDialog
        open={confirmRecompute}
        title={t("pages.editor.recompute_title")}
        confirmLabel={t("pages.editor.recompute")}
        danger
        onConfirm={() => void recompute()}
        onCancel={() => setConfirmRecompute(false)}
      >
        <p>{t("pages.editor.recompute_body")}</p>
      </ConfirmDialog>
    </div>
  );
}
