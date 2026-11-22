import {
  ArrowRight,
  Copy,
  Hourglass,
  Pause,
  Play,
  Plus,
  Redo2,
  Repeat,
  RotateCw,
  Smartphone,
  Sparkles,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { MomentCard } from "../components/moments/MomentCard";
import { Player } from "../components/moments/Player";
import { Minimap, ZoomTimeline } from "../components/moments/Timeline";
import { TranscriptPanel } from "../components/moments/TranscriptPanel";
import { EmptyState, ErrorState, Skeleton } from "../components/states";
import { TimeField } from "../components/TimeField";
import { useToast } from "../components/Toasts";
import { Tooltip } from "../components/Tooltip";
import {
  ApiError,
  CAPTION_STYLES,
  PLATFORMS,
  apiUrl,
  createMoment,
  deleteMoment,
  findMoments,
  getAnalysis,
  getTranscript,
  getWaveform,
  listMoments,
  patchMoment,
  type Analysis,
  type ApiIssue,
  type Moment,
  type MomentPatch,
  type Waveform,
  type Word,
} from "../lib/api";
import { translateIssue } from "../lib/errors";
import { formatDuration, formatTimecode } from "../lib/format";
import {
  clampRange,
  countByStatus,
  filterMoments,
  frameStep,
  historyPush,
  historyRedo,
  historyUndo,
  sortMoments,
  type HistoryState,
  type MomentFilter,
  type MomentSort,
} from "../lib/moments";
import { useSaveStatus } from "../state/save";
import { useSources } from "../state/sources";

type Entry =
  | { kind: "patch"; id: number; before: MomentPatch; after: MomentPatch }
  | { kind: "create"; moment: Moment }
  | { kind: "delete"; moment: Moment };

interface SourceData {
  words: Word[];
  analysis: Analysis | null;
  waveform: Waveform | null;
}

const FILTERS: MomentFilter[] = ["all", "proposed", "approved", "rejected"];
const SAVE_DELAY_MS = 500;
const POSITION_KEY = (id: string) => `shortforge.moments.${id}`;

function readPosition(sourceId: string): { selected: number | null; time: number } {
  try {
    const raw = window.localStorage.getItem(POSITION_KEY(sourceId));
    if (raw) return { selected: null, time: 0, ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { selected: null, time: 0 };
}

function writePosition(sourceId: string, selected: number | null, time: number) {
  try {
    window.localStorage.setItem(POSITION_KEY(sourceId), JSON.stringify({ selected, time: Math.round(time * 100) / 100 }));
  } catch {
    /* ignore */
  }
}

async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/** Moments Review — checkpoint 1. */
export default function MomentsReview() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  const { track } = useSaveStatus();
  const { sources, loaded: sourcesLoaded, currentSource: source, setCurrentSourceId, refresh } = useSources();

  const [data, setData] = useState<SourceData | null>(null);
  const [moments, setMoments] = useState<Moment[]>([]);
  const [loadError, setLoadError] = useState<ApiIssue | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filter, setFilter] = useState<MomentFilter>("all");
  const [sort, setSort] = useState<MomentSort>("score");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [preview916, setPreview916] = useState(false);
  const [history, setHistory] = useState<HistoryState<Entry>>({ past: [], future: [] });
  const [snappedFlash, setSnappedFlash] = useState<{ id: number; start: number; end: number } | null>(null);
  const [findOpen, setFindOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const video = useRef<HTMLVideoElement>(null);
  const pending = useRef(new Map<number, MomentPatch>());
  const timers = useRef(new Map<number, number>());
  const idMap = useRef(new Map<number, number>());
  const loadedFor = useRef<string | null>(null);
  const restoreTime = useRef<number | null>(null);

  const sourceId = source?.id ?? null;
  const duration = source?.duration ?? 0;

  const fail = useCallback(
    (err: unknown) => {
      const issue = err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} };
      toast.show(translateIssue(t, issue), "error");
    },
    [t, toast],
  );

  // ------------------------------------------------------------------ loading
  const load = useCallback(async (id: string, keepSelection = false) => {
    setLoading(true);
    setLoadError(null);
    try {
      const [transcript, analysis, waveform, list] = await Promise.all([
        optional(getTranscript(id)),
        optional(getAnalysis(id)),
        optional(getWaveform(id)),
        listMoments(id),
      ]);
      setData({ words: transcript?.words ?? [], analysis, waveform });
      setMoments(list);
      if (!keepSelection) {
        const saved = readPosition(id);
        const pick = list.find((m) => m.id === saved.selected) ?? sortMoments(list, "score")[0] ?? null;
        setSelectedId(pick?.id ?? null);
        restoreTime.current = saved.selected === pick?.id ? saved.time : (pick?.start ?? saved.time);
      }
      loadedFor.current = id;
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error", params: {} });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!sourceId) return;
    setHistory({ past: [], future: [] });
    idMap.current.clear();
    void load(sourceId);
  }, [sourceId, load]);

  // New proposals (moments.json imported, re-run finished) -> reload the list, unless edits are pending.
  const sourceStamp = `${source?.moments_provider}|${source?.steps.moments?.state}|${source?.moments.total}`;
  const lastStamp = useRef(sourceStamp);
  useEffect(() => {
    if (!sourceId || loadedFor.current !== sourceId) {
      lastStamp.current = sourceStamp;
      return;
    }
    if (lastStamp.current === sourceStamp) return;
    lastStamp.current = sourceStamp;
    if (pending.current.size === 0) {
      void listMoments(sourceId)
        .then((list) => {
          setMoments(list);
          setSelectedId((sel) => (list.some((m) => m.id === sel) ? sel : (sortMoments(list, "score")[0]?.id ?? null)));
        })
        .catch(() => undefined);
    }
  }, [sourceStamp, sourceId]);

  // Pick a source automatically when none is selected (most recent ready/waiting one).
  useEffect(() => {
    if (source || !sourcesLoaded) return;
    const candidate = sources.find((s) => s.status === "ready" || s.status === "waiting");
    if (candidate) setCurrentSourceId(candidate.id);
  }, [source, sources, sourcesLoaded, setCurrentSourceId]);

  const selected = useMemo(() => moments.find((m) => m.id === selectedId) ?? null, [moments, selectedId]);
  const visible = useMemo(() => sortMoments(filterMoments(moments, filter), sort), [moments, filter, sort]);
  const counts = useMemo(() => countByStatus(moments), [moments]);

  // Remember the position ("← Moments" returns to the exact place).
  useEffect(() => {
    if (!sourceId || loadedFor.current !== sourceId) return;
    const handle = window.setTimeout(() => writePosition(sourceId, selectedId, time), 300);
    return () => window.clearTimeout(handle);
  }, [sourceId, selectedId, time]);

  // ------------------------------------------------------------------ saving
  const resolve = (id: number) => {
    let cur = id;
    for (let guard = 0; idMap.current.has(cur) && guard < 200; guard++) cur = idMap.current.get(cur)!;
    return cur;
  };

  const applyLocal = useCallback((id: number, fields: MomentPatch) => {
    const { snap: _snap, ...rest } = fields;
    void _snap;
    setMoments((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...rest, duration: (rest.end ?? m.end) - (rest.start ?? m.start) } : m)),
    );
  }, []);

  const flush = useCallback(
    async (id: number) => {
      const timer = timers.current.get(id);
      if (timer) window.clearTimeout(timer);
      timers.current.delete(id);
      const patch = pending.current.get(id);
      if (!patch) return;
      pending.current.delete(id);
      try {
        const saved = await track(patchMoment(id, patch));
        if (!pending.current.has(id)) {
          setMoments((prev) => prev.map((m) => (m.id === id ? saved : m)));
          if (patch.snap && patch.start !== undefined) {
            setSnappedFlash({ id, start: saved.start, end: saved.end });
          }
        }
      } catch (err) {
        fail(err);
      }
    },
    [fail, track],
  );

  const queueSave = useCallback(
    (id: number, patch: MomentPatch) => {
      const merged = { ...(pending.current.get(id) ?? {}), ...patch };
      if (patch.start === undefined && patch.end === undefined && pending.current.get(id)?.snap !== undefined) {
        merged.snap = pending.current.get(id)!.snap;
      }
      pending.current.set(id, merged);
      const old = timers.current.get(id);
      if (old) window.clearTimeout(old);
      timers.current.set(id, window.setTimeout(() => void flush(id), SAVE_DELAY_MS));
    },
    [flush],
  );

  const flushAll = useCallback(async () => {
    await Promise.all([...pending.current.keys()].map((id) => flush(id)));
  }, [flush]);

  // Save pending edits when leaving the page.
  useEffect(() => () => void flushAll(), [flushAll]);

  useEffect(() => {
    if (!snappedFlash) return;
    const handle = window.setTimeout(() => setSnappedFlash(null), 2500);
    return () => window.clearTimeout(handle);
  }, [snappedFlash]);

  /** A user edit: applied locally now, saved after 500 ms, recorded for undo. */
  const edit = useCallback(
    (id: number, after: MomentPatch) => {
      const m = moments.find((x) => x.id === id);
      if (!m) return;
      const before: MomentPatch = {};
      for (const key of Object.keys(after) as (keyof MomentPatch)[]) {
        if (key !== "snap") (before as Record<string, unknown>)[key] = m[key as keyof Moment];
      }
      applyLocal(id, after);
      queueSave(id, after);
      setHistory((h) => historyPush(h, { kind: "patch", id, before, after }));
    },
    [moments, applyLocal, queueSave],
  );

  const recreate = useCallback(
    async (m: Moment) => {
      if (!sourceId) return null;
      const created = await track(
        createMoment(sourceId, { start: m.start, end: m.end, title: m.title, hook: m.hook, status: m.status, snap: false }),
      );
      idMap.current.set(m.id, created.id);
      setMoments((prev) => [...prev.filter((x) => x.id !== m.id), created]);
      return created;
    },
    [sourceId, track],
  );

  const remove = useCallback(
    async (id: number) => {
      const timer = timers.current.get(id);
      if (timer) window.clearTimeout(timer);
      timers.current.delete(id);
      pending.current.delete(id);
      await track(deleteMoment(id));
      setMoments((prev) => prev.filter((x) => x.id !== id));
      setSelectedId((sel) => (sel === id ? null : sel));
    },
    [track],
  );

  const runEntry = useCallback(
    async (entry: Entry, direction: "undo" | "redo") => {
      try {
        if (entry.kind === "patch") {
          const id = resolve(entry.id);
          const fields = direction === "undo" ? entry.before : entry.after;
          // undo restores exact values; redo repeats the original request (snapping again if it did)
          const patch = direction === "undo" ? { ...fields, snap: false } : fields;
          applyLocal(id, patch);
          queueSave(id, patch);
          setSelectedId(id);
        } else {
          const create = (entry.kind === "create") === (direction === "redo");
          if (create) {
            const created = await recreate({ ...entry.moment, id: resolve(entry.moment.id) });
            if (created) setSelectedId(created.id);
          } else {
            await remove(resolve(entry.moment.id));
          }
        }
      } catch (err) {
        fail(err);
      }
    },
    [applyLocal, queueSave, recreate, remove, fail],
  );

  const undo = useCallback(() => {
    const step = historyUndo(history);
    if (!step) return;
    setHistory(step.state);
    void runEntry(step.entry, "undo");
  }, [history, runEntry]);

  const redo = useCallback(() => {
    const step = historyRedo(history);
    if (!step) return;
    setHistory(step.state);
    void runEntry(step.entry, "redo");
  }, [history, runEntry]);

  // ------------------------------------------------------------------ actions
  const setStatus = useCallback((id: number, status: Moment["status"]) => edit(id, { status }), [edit]);

  const setRange = useCallback(
    (id: number, start: number, end: number, snap = true) => {
      const r = clampRange(start, end, duration, "both");
      edit(id, { start: r.start, end: r.end, snap });
    },
    [edit, duration],
  );

  const createManual = useCallback(
    async (start: number, end: number, text = "") => {
      if (!sourceId) return;
      const r = clampRange(start, end, duration, "end", 1);
      const title = text.length > 60 ? `${text.slice(0, 57).trimEnd()}…` : text;
      try {
        const created = await track(createMoment(sourceId, { start: r.start, end: r.end, ...(title ? { title } : {}) }));
        setMoments((prev) => [...prev, created]);
        setSelectedId(created.id);
        setHistory((h) => historyPush(h, { kind: "create", moment: created }));
        toast.show(t("pages.moments.created"), "success");
      } catch (err) {
        fail(err);
      }
    },
    [sourceId, duration, track, toast, t, fail],
  );

  const deleteManual = useCallback(
    async (id: number) => {
      const m = moments.find((x) => x.id === id);
      if (!m) return;
      try {
        await remove(id);
        setHistory((h) => historyPush(h, { kind: "delete", moment: m }));
      } catch (err) {
        fail(err);
      }
    },
    [moments, remove, fail],
  );

  const approveAndOpen = async () => {
    if (!selected) return;
    if (selected.status !== "approved") edit(selected.id, { status: "approved" });
    await flushAll();
    navigate(`/editor?moment=${selected.id}`);
  };

  const findAgain = async () => {
    if (!sourceId) return;
    setFindOpen(false);
    setBusy(true);
    try {
      await flushAll();
      await findMoments(sourceId);
      toast.show(t("pages.moments.find_started"), "success");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const copyCommand = async () => {
    if (!source) return;
    try {
      await navigator.clipboard.writeText(source.find_moments_command);
      toast.show(t("pages.library.command_copied"), "success");
    } catch {
      toast.show(t("pages.library.command_copy_failed", { command: source.find_moments_command }), "error");
    }
  };

  // ------------------------------------------------------------------ playback
  const seek = useCallback((at: number) => {
    const v = video.current;
    const clamped = Math.max(0, Math.min(at, v?.duration || at));
    if (v) v.currentTime = clamped;
    setTime(clamped);
  }, []);

  const selectMoment = useCallback(
    (id: number, jump = true) => {
      setSelectedId(id);
      const m = moments.find((x) => x.id === id);
      if (m && jump) seek(m.start);
    },
    [moments, seek],
  );

  const play = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (selected && (v.currentTime < selected.start - 0.05 || v.currentTime >= selected.end - 0.05)) v.currentTime = selected.start;
    void v.play().catch(() => undefined);
  }, [selected]);

  const togglePlay = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) play();
    else v.pause();
  }, [play]);

  // Restore the saved position once the video can seek.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const onLoaded = () => {
      if (restoreTime.current != null) {
        v.currentTime = restoreTime.current;
        setTime(restoreTime.current);
        restoreTime.current = null;
      }
    };
    v.addEventListener("loadedmetadata", onLoaded);
    if (v.readyState >= 1) onLoaded();
    return () => v.removeEventListener("loadedmetadata", onLoaded);
  }, [source?.proxy_url, data]);

  // Track the playhead; keep playback inside the selected moment (loop or stop at the out point).
  const bounds = useRef<{ start: number; end: number } | null>(null);
  bounds.current = selected ? { start: selected.start, end: selected.end } : null;
  const loopRef = useRef(loop);
  loopRef.current = loop;
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    let raf = 0;
    let last = -1;
    const tick = () => {
      const now = v.currentTime;
      const b = bounds.current;
      if (b && !v.paused && now >= b.end) {
        if (loopRef.current) v.currentTime = b.start;
        else {
          v.pause();
          v.currentTime = b.end;
        }
      }
      if (Math.abs(now - last) >= 1 / 30) {
        last = now;
        setTime(now);
      }
      raf = window.requestAnimationFrame(tick);
    };
    const onPlay = () => {
      setPlaying(true);
      raf = window.requestAnimationFrame(tick);
    };
    const onPause = () => {
      setPlaying(false);
      window.cancelAnimationFrame(raf);
      setTime(v.currentTime);
    };
    const onSeeked = () => setTime(v.currentTime);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("seeked", onSeeked);
    return () => {
      window.cancelAnimationFrame(raf);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("seeked", onSeeked);
    };
  }, [source?.proxy_url, data]);

  // ------------------------------------------------------------------ keyboard
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (isTyping(e.target) || e.altKey || e.metaKey) return;
    const v = video.current;
    const ctrl = e.ctrlKey;
    const key = e.key.toLowerCase();
    if (ctrl && (key === "z" || key === "y")) {
      e.preventDefault();
      if (key === "y" || e.shiftKey) redo();
      else undo();
      return;
    }
    if (ctrl) return;
    const index = selected ? visible.findIndex((m) => m.id === selected.id) : -1;
    switch (key) {
      case " ":
        e.preventDefault();
        togglePlay();
        break;
      case "k":
        if (v) {
          v.pause();
          v.playbackRate = 1;
        }
        break;
      case "l":
        if (v) {
          if (v.paused) {
            v.playbackRate = 1;
            play();
          } else v.playbackRate = Math.min(4, v.playbackRate * 2);
        }
        break;
      case "j":
        if (v) {
          v.playbackRate = 1;
          seek(Math.max(selected?.start ?? 0, v.currentTime - 2));
        }
        break;
      case "arrowleft":
      case "arrowright": {
        e.preventDefault();
        if (!v) break;
        v.pause();
        const step = e.shiftKey ? 1 : frameStep(source?.fps);
        seek(v.currentTime + (key === "arrowleft" ? -step : step));
        break;
      }
      case "i":
        if (selected) setRange(selected.id, v?.currentTime ?? time, selected.end);
        break;
      case "o":
        if (selected) setRange(selected.id, selected.start, v?.currentTime ?? time);
        break;
      case "a":
        if (selected) setStatus(selected.id, "approved");
        break;
      case "r":
        if (selected) setStatus(selected.id, "rejected");
        break;
      case "arrowup":
      case "arrowdown": {
        e.preventDefault();
        if (!visible.length) break;
        const next = key === "arrowup" ? Math.max(0, index - 1) : Math.min(visible.length - 1, index + 1);
        selectMoment(visible[index === -1 ? 0 : next].id);
        break;
      }
      default:
        return;
    }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Scroll the selected card into view.
  useEffect(() => {
    if (selectedId == null) return;
    document.querySelector(`[data-moment-id="${selectedId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  // ------------------------------------------------------------------ render
  if (!sourcesLoaded) {
    return (
      <div className="p-6">
        <Skeleton rows={6} />
      </div>
    );
  }
  if (!source) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <EmptyState icon={Sparkles} message={t("pages.moments.empty")} action={{ label: t("pages.moments.action"), onClick: () => navigate("/library") }} />
      </div>
    );
  }
  if (loadError) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <ErrorState
          message={translateIssue(t, loadError)}
          onRetry={() => void load(source.id)}
          log={`${loadError.code} ${JSON.stringify(loadError.params)}`}
        />
      </div>
    );
  }
  if (!data || loading && loadedFor.current !== source.id) {
    return (
      <div className="grid h-full grid-cols-[300px_minmax(0,1fr)_380px] gap-4 p-4" data-testid="moments-loading">
        <Skeleton rows={8} />
        <div className="aspect-video animate-pulse rounded-lg bg-white/5" />
        <Skeleton rows={10} />
      </div>
    );
  }

  const notReady = !source.has_transcript && source.status !== "ready" && source.status !== "waiting";
  const waiting = source.status === "waiting";
  const momentNotices = source.steps.moments?.notices ?? [];

  return (
    <div className="grid h-full min-h-0 grid-cols-[300px_minmax(0,1fr)_380px]" data-testid="moments-page">
      {/* ------------------------------------------------ list */}
      <aside className="flex min-h-0 flex-col border-r border-line bg-panel/40">
        <div className="space-y-3 border-b border-line p-3">
          <select
            value={source.id}
            onChange={(e) => setCurrentSourceId(e.target.value)}
            aria-label={t("pages.moments.source")}
            data-testid="moments-source-select"
            className="w-full truncate rounded-md border border-line bg-app px-2 py-1.5 text-xs text-fg outline-none focus:border-accent"
          >
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <div className="flex flex-wrap gap-1" role="tablist" aria-label={t("pages.moments.filter")}>
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                role="tab"
                aria-selected={filter === f}
                data-testid={`filter-${f}`}
                onClick={() => setFilter(f)}
                className={`rounded-md px-2 py-1 text-[11px] ${filter === f ? "bg-accent text-white" : "text-muted hover:bg-white/5 hover:text-fg"}`}
              >
                {t(`pages.moments.filter_${f}`)} <span className="font-mono opacity-75">{counts[f]}</span>
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex min-w-0 flex-1 items-center gap-1.5 text-[11px] text-muted">
              {t("pages.moments.sort")}
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as MomentSort)}
                data-testid="sort-select"
                className="min-w-0 flex-1 rounded-md border border-line bg-app px-1.5 py-1 text-[11px] text-fg outline-none"
              >
                <option value="score">{t("pages.moments.sort_score")}</option>
                <option value="time">{t("pages.moments.sort_time")}</option>
              </select>
            </label>
            <Tooltip label={t("pages.moments.new_moment_hint")} side="bottom-end">
              <button
                type="button"
                data-testid="new-moment"
                onClick={() => void createManual(time, time + 15)}
                className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] hover:bg-white/5"
              >
                <Plus className="size-3" />
                {t("pages.moments.new_moment")}
              </button>
            </Tooltip>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3" data-testid="moment-list">
          {waiting && (
            <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/5 p-3 text-xs" data-testid="moments-waiting">
              <div className="flex items-center gap-2 font-medium text-warning">
                <Hourglass className="size-3.5" />
                {t("pages.library.waiting_title")}
              </div>
              <p className="text-[11px] leading-relaxed text-muted">{t("pages.library.waiting_body")}</p>
              <button
                type="button"
                onClick={() => void copyCommand()}
                className="flex w-full items-center justify-between gap-2 rounded-md border border-line bg-app px-2 py-1 font-mono text-[11px] hover:border-accent"
              >
                <span className="truncate">{source.find_moments_command}</span>
                <Copy className="size-3 shrink-0" />
              </button>
              {source.steps.moments?.error && <p className="text-[11px] text-danger">{translateIssue(t, source.steps.moments.error)}</p>}
            </div>
          )}
          {momentNotices.map((n) => (
            <p key={n.code} className="rounded-md border border-line px-2.5 py-1.5 text-[11px] text-muted">
              {translateIssue(t, n)}
            </p>
          ))}
          {source.moments_notes && <p className="rounded-md border border-line px-2.5 py-1.5 text-[11px] text-muted">{source.moments_notes}</p>}
          {visible.length === 0 && !waiting && (
            <div className="px-1 py-6 text-center text-xs text-muted" data-testid="moments-list-empty">
              {notReady ? t("pages.moments.not_ready") : moments.length ? t("pages.moments.filter_empty") : t("pages.moments.none_yet")}
            </div>
          )}
          {visible.map((m) => (
            <MomentCard
              key={m.id}
              moment={m}
              selected={m.id === selectedId}
              onSelect={selectMoment}
              onSetStatus={setStatus}
              onDelete={(id) => void deleteManual(id)}
            />
          ))}
        </div>
      </aside>

      {/* ------------------------------------------------ player + timeline */}
      <section className="flex min-h-0 min-w-0 flex-col">
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          <div className="mx-auto w-full max-w-[min(100%,calc((100vh-330px)*16/9))]">
            <Player
              ref={video}
              src={source.proxy_url ? apiUrl(source.proxy_url) : null}
              sourceWidth={source.width}
              sourceHeight={source.height}
              preview916={preview916}
              onTogglePlay={togglePlay}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Tooltip label={playing ? t("pages.moments.pause") : t("pages.moments.play")} shortcut="Space" side="top">
              <button
                type="button"
                onClick={togglePlay}
                aria-label={playing ? t("pages.moments.pause") : t("pages.moments.play")}
                data-testid="play-button"
                className="flex size-8 items-center justify-center rounded-full bg-accent text-white hover:bg-accent/90"
              >
                {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
              </button>
            </Tooltip>
            <span className="font-mono text-xs text-muted" data-testid="playhead">
              <span className="text-fg">{formatTimecode(time)}</span> / {formatTimecode(duration)}
            </span>
            <Tooltip label={t("pages.moments.loop")} side="top">
              <button
                type="button"
                aria-pressed={loop}
                onClick={() => setLoop((v) => !v)}
                data-testid="loop-toggle"
                className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${loop ? "border-accent text-accent" : "border-line text-muted"}`}
              >
                <Repeat className="size-3.5" />
                {t("pages.moments.loop_short")}
              </button>
            </Tooltip>
            <Tooltip label={t("pages.moments.preview916_hint")} side="top">
              <button
                type="button"
                aria-pressed={preview916}
                onClick={() => setPreview916((v) => !v)}
                data-testid="preview916-toggle"
                className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] ${preview916 ? "border-accent text-accent" : "border-line text-muted"}`}
              >
                <Smartphone className="size-3.5" />
                {t("pages.moments.preview916")}
              </button>
            </Tooltip>
            <div className="ml-auto flex items-center gap-1">
              <Tooltip label={t("pages.moments.undo")} shortcut="Ctrl+Z" side="top">
                <button
                  type="button"
                  aria-label={t("pages.moments.undo")}
                  disabled={!history.past.length}
                  onClick={undo}
                  data-testid="undo-button"
                  className="rounded-md p-1.5 text-muted hover:bg-white/5 hover:text-fg disabled:opacity-30"
                >
                  <Undo2 className="size-4" />
                </button>
              </Tooltip>
              <Tooltip label={t("pages.moments.redo")} shortcut="Ctrl+Shift+Z" side="top">
                <button
                  type="button"
                  aria-label={t("pages.moments.redo")}
                  disabled={!history.future.length}
                  onClick={redo}
                  data-testid="redo-button"
                  className="rounded-md p-1.5 text-muted hover:bg-white/5 hover:text-fg disabled:opacity-30"
                >
                  <Redo2 className="size-4" />
                </button>
              </Tooltip>
              <Tooltip label={t("pages.moments.find_again_hint")} side="top">
                <button
                  type="button"
                  disabled={busy || source.active_job !== null || !source.has_transcript}
                  onClick={() => setFindOpen(true)}
                  data-testid="find-again"
                  className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-muted hover:bg-white/5 hover:text-fg disabled:opacity-40"
                >
                  <RotateCw className="size-3.5" />
                  {t("pages.moments.find_again")}
                </button>
              </Tooltip>
            </div>
          </div>

          <Minimap duration={duration} moments={moments} selectedId={selectedId} time={time} onSeek={seek} onSelect={(id) => selectMoment(id, false)} />

          {selected ? (
            <>
              <ZoomTimeline
                moment={selected}
                duration={duration}
                words={data.words}
                analysis={data.analysis}
                waveform={data.waveform}
                time={time}
                snappedFlash={snappedFlash && snappedFlash.id === selected.id ? snappedFlash : null}
                onSeek={seek}
                onPreviewRange={(start) => seek(start)}
                onCommitRange={(start, end, snap, edge) => {
                  setRange(selected.id, start, end, snap);
                  seek(edge === "start" ? start : Math.max(start, end - 2));
                }}
              />
              <div className="grid grid-cols-[auto_auto_auto_minmax(0,1fr)] items-center gap-x-4 gap-y-2">
                <label className="flex items-center gap-2 text-[11px] text-muted">
                  <Tooltip label={t("pages.moments.set_in")} shortcut="I" side="top">
                    <span>{t("pages.moments.in_point")}</span>
                  </Tooltip>
                  <TimeField
                    label={t("pages.moments.in_point")}
                    testId="in-field"
                    value={selected.start}
                    max={selected.end - 0.5}
                    onChange={(v) => setRange(selected.id, v, selected.end)}
                  />
                </label>
                <label className="flex items-center gap-2 text-[11px] text-muted">
                  <Tooltip label={t("pages.moments.set_out")} shortcut="O" side="top">
                    <span>{t("pages.moments.out_point")}</span>
                  </Tooltip>
                  <TimeField
                    label={t("pages.moments.out_point")}
                    testId="out-field"
                    value={selected.end}
                    min={selected.start + 0.5}
                    max={duration || Infinity}
                    onChange={(v) => setRange(selected.id, selected.start, v)}
                  />
                </label>
                <span className="font-mono text-xs text-muted" data-testid="moment-duration">
                  {t("pages.moments.length", { value: formatDuration(selected.end - selected.start) })}
                </span>
                <span />
                <TextField
                  key={`title-${selected.id}`}
                  label={t("pages.moments.title_label")}
                  value={selected.title}
                  testId="title-field"
                  onCommit={(v) => edit(selected.id, { title: v })}
                />
                <TextField
                  key={`hook-${selected.id}`}
                  label={t("pages.moments.hook_label")}
                  value={selected.hook}
                  testId="hook-field"
                  onCommit={(v) => edit(selected.id, { hook: v })}
                />
              </div>
            </>
          ) : (
            <p className="rounded-md border border-dashed border-line p-4 text-center text-xs text-muted">{t("pages.moments.select_hint")}</p>
          )}
        </div>

        {/* bottom bar */}
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-t border-line bg-panel px-4 py-2.5" data-testid="moments-bottom-bar">
          <div className="flex items-center gap-1.5 text-[11px] text-muted">
            {t("pages.moments.caption_style")}
            <div className="flex rounded-md border border-line bg-app p-0.5" role="radiogroup" aria-label={t("pages.moments.caption_style")}>
              {CAPTION_STYLES.map((style) => (
                <button
                  key={style}
                  type="button"
                  role="radio"
                  aria-checked={selected?.caption_style === style}
                  disabled={!selected}
                  data-testid={`caption-${style}`}
                  onClick={() => selected && edit(selected.id, { caption_style: style })}
                  className={`rounded px-2 py-0.5 text-[11px] ${selected?.caption_style === style ? "bg-accent text-white" : "text-muted hover:text-fg"} disabled:opacity-40`}
                >
                  {t(`caption_style.${style}`)}
                </button>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-1.5 text-[11px] text-muted">
            {t("pages.moments.platform")}
            <select
              value={selected?.platform ?? "youtube_shorts"}
              disabled={!selected}
              onChange={(e) => selected && edit(selected.id, { platform: e.target.value as Moment["platform"] })}
              data-testid="platform-select"
              className="rounded-md border border-line bg-app px-1.5 py-1 text-[11px] text-fg outline-none disabled:opacity-40"
            >
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {t(`platform.${p}`)}
                </option>
              ))}
            </select>
          </label>
          <Tooltip label={t("pages.moments.approve_open_hint")} side="top">
            <button
              type="button"
              disabled={!selected}
              onClick={() => void approveAndOpen()}
              data-testid="approve-open"
              className="ml-auto flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent/90 disabled:opacity-40"
            >
              {t("pages.moments.approve_open")}
              <ArrowRight className="size-3.5" />
            </button>
          </Tooltip>
        </div>
      </section>

      {/* ------------------------------------------------ transcript */}
      <aside className="flex min-h-0 flex-col border-l border-line bg-panel/40">
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-xs font-semibold tracking-wide text-muted uppercase">{t("pages.moments.transcript")}</h2>
          <span className="truncate text-[11px] text-muted">{t("pages.moments.select_to_create")}</span>
        </div>
        <div className="min-h-0 flex-1">
          <TranscriptPanel
            words={data.words}
            time={time}
            range={selected ? { start: selected.start, end: selected.end } : null}
            playing={playing}
            onSeek={seek}
            onCreate={(start, end, text) => void createManual(start, end, text)}
          />
        </div>
      </aside>

      <ConfirmDialog
        open={findOpen}
        title={t("pages.moments.find_again_title")}
        confirmLabel={t("pages.moments.find_again")}
        busy={busy}
        onCancel={() => setFindOpen(false)}
        onConfirm={() => void findAgain()}
      >
        <p>{t("pages.moments.find_again_body")}</p>
      </ConfirmDialog>
    </div>
  );
}

/** Inline text input that commits on blur / Enter (one undo step per edit, not per keystroke). */
function TextField({ label, value, onCommit, testId }: { label: string; value: string; onCommit: (v: string) => void; testId: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (text.trim() !== value.trim()) onCommit(text.trim());
  };
  return (
    <label className="col-span-2 flex min-w-0 items-center gap-2 text-[11px] text-muted">
      <span className="shrink-0">{label}</span>
      <input
        value={text}
        data-testid={testId}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setText(value);
            (e.target as HTMLInputElement).blur();
          }
        }}
        className="min-w-0 flex-1 rounded-md border border-line bg-app px-2 py-1 text-xs text-fg outline-none focus:border-accent"
      />
    </label>
  );
}
