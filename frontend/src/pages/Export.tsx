import {
  AlertTriangle,
  Clapperboard,
  Download,
  FileArchive,
  FileText,
  FolderOpen,
  Package,
  Pencil,
  Play,
  RotateCcw,
  ScrollText,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { JobLog } from "../components/JobLog";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "../components/states";
import { useToast } from "../components/Toasts";
import { Tooltip } from "../components/Tooltip";
import {
  ApiError,
  cancelRender,
  exportCapcut,
  getEncoders,
  importEdited,
  listExports,
  openClipFolder,
  outputUrl,
  remainingSeconds,
  renderBatch,
  renderClip,
  type ApiIssue,
  type EncoderLabel,
  type ExportClip,
  type ExportTask,
  type OutputKind,
} from "../lib/api";
import { translateIssue } from "../lib/errors";
import { formatDateTime, formatDuration, formatPercent } from "../lib/format";
import { useSources } from "../state/sources";

const FAST_POLL_MS = 1500;
const SLOW_POLL_MS = 5000;
const isActive = (task: ExportTask<unknown>) => task.state === "queued" || task.state === "running";
const busy = (c: ExportClip) => isActive(c.render) || isActive(c.capcut) || c.busy;

const btn =
  "flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs hover:border-accent disabled:cursor-not-allowed disabled:opacity-40";
const primaryBtn =
  "flex items-center gap-1.5 rounded-md btn-primary px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40";

/** Export: render queue, finished clips, CapCut bundle + round-trip import, batch render. */
export default function Export() {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { sources } = useSources();
  const [items, setItems] = useState<ExportClip[] | null>(null);
  const [error, setError] = useState<ApiIssue | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<{ ids: string[]; existing: number } | null>(null);
  const [playing, setPlaying] = useState<{ clipId: string; kind: OutputKind; title: string; version?: number } | null>(null);
  const [uploads, setUploads] = useState<Record<string, number>>({});
  const [encoder, setEncoder] = useState<EncoderLabel | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const previous = useRef(new Map<string, { render: string; capcut: string }>());
  const fileInput = useRef<HTMLInputElement>(null);
  const importTarget = useRef<string | null>(null);

  const titleOf = useCallback((c: ExportClip) => c.title || t("pages.export.untitled"), [t]);

  const load = useCallback(async () => {
    try {
      const list = await listExports();
      // toasts when a job of this page's session finishes
      for (const c of list) {
        const before = previous.current.get(c.id);
        if (before) {
          if (before.render !== c.render.state && (before.render === "running" || before.render === "queued")) {
            if (c.render.state === "done") toast.show(t("pages.export.done_toast", { title: titleOf(c) }), "success");
            if (c.render.state === "error") toast.show(t("pages.export.failed_toast", { title: titleOf(c) }), "error");
          }
          if (before.capcut !== c.capcut.state && before.capcut === "running" && c.capcut.state === "done")
            toast.show(t("pages.export.capcut_done_toast", { title: titleOf(c) }), "success");
        }
        previous.current.set(c.id, { render: c.render.state, capcut: c.capcut.state });
      }
      setItems(list);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.issue : { code: "INTERNAL_ERROR", severity: "error", params: {} });
    }
  }, [t, toast, titleOf]);

  const anyActive = (items ?? []).some((c) => isActive(c.render) || isActive(c.capcut));
  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), anyActive ? FAST_POLL_MS : SLOW_POLL_MS);
    return () => window.clearInterval(id);
  }, [load, anyActive]);
  useEffect(() => {
    if (!anyActive) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyActive]);
  useEffect(() => {
    getEncoders()
      .then((e) => setEncoder(e.labels[0] ?? null))
      .catch(() => {});
  }, []);

  const queue = useMemo(() => (items ?? []).filter((c) => isActive(c.render) || isActive(c.capcut)), [items]);
  const selectable = useMemo(() => (items ?? []).filter((c) => !busy(c)), [items]);
  const selectedIds = selectable.filter((c) => selected.has(c.id)).map((c) => c.id);
  const allSelected = selectable.length > 0 && selectedIds.length === selectable.length;

  const fail = (err: unknown) => {
    if (err instanceof ApiError) toast.show(translateIssue(t, err.issue), "error");
  };

  const queueRender = async (ids: string[]) => {
    setConfirm(null);
    try {
      if (ids.length === 1) {
        await renderClip(ids[0]);
        toast.show(t("pages.export.queued_toast"), "success");
      } else {
        const r = await renderBatch(ids);
        toast.show(t("pages.export.batch_toast", { count: r.queued.length }), "success");
        if (r.skipped.length) toast.show(t("pages.export.batch_skipped", { count: r.skipped.length }), "info");
      }
      setSelected(new Set());
      await load();
    } catch (err) {
      fail(err);
    }
  };

  const askRender = (ids: string[]) => {
    const existing = (items ?? []).filter((c) => ids.includes(c.id) && c.files.final).length;
    if (existing > 0) setConfirm({ ids, existing });
    else void queueRender(ids);
  };

  const act = async (fn: () => Promise<unknown>, okMessage?: string) => {
    try {
      await fn();
      if (okMessage) toast.show(okMessage, "success");
      await load();
    } catch (err) {
      fail(err);
    }
  };

  const startImport = (clipId: string) => {
    importTarget.current = clipId;
    fileInput.current?.click();
  };

  const onImportFile = async (file: File | undefined) => {
    const clipId = importTarget.current;
    if (fileInput.current) fileInput.current.value = "";
    if (!file || !clipId) return;
    setUploads((u) => ({ ...u, [clipId]: 0 }));
    try {
      await importEdited(clipId, file, (f) => setUploads((u) => ({ ...u, [clipId]: f })));
      toast.show(t("pages.export.imported_toast"), "success");
      await load();
    } catch (err) {
      fail(err);
    } finally {
      setUploads((u) => {
        const next = { ...u };
        delete next[clipId];
        return next;
      });
    }
  };

  const sourceName = (id: string) => sources.find((s) => s.id === id)?.name ?? id;

  let body: ReactNode;
  if (error && items === null) {
    body = <ErrorState message={translateIssue(t, error)} onRetry={() => void load()} />;
  } else if (items === null) {
    body = <Skeleton rows={6} />;
  } else if (items.length === 0) {
    body = (
      <EmptyState icon={Download} message={t("pages.export.empty")} action={{ label: t("pages.export.action"), onClick: () => navigate("/moments") }} />
    );
  } else {
    body = (
      <div className="space-y-8">
        <section data-testid="render-queue">
          <h2 className="mb-3 text-sm font-semibold text-muted">{t("pages.export.queue")}</h2>
          {queue.length === 0 ? (
            <p className="rounded-lg border border-dashed border-line px-4 py-3 text-sm text-muted">{t("pages.export.queue_empty")}</p>
          ) : (
            <ul className="space-y-2">
              {queue.map((c) => (
                <QueueRow key={c.id} clip={c} title={titleOf(c)} source={sourceName(c.source_id)} encoder={encoder} now={now} onCancel={() => void act(() => cancelRender(c.id), t("pages.export.cancelled_toast"))} />
              ))}
            </ul>
          )}
        </section>

        <section data-testid="export-clips">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm font-semibold text-muted">
              <input
                type="checkbox"
                data-testid="select-all"
                checked={allSelected}
                disabled={selectable.length === 0}
                onChange={(e) => setSelected(e.target.checked ? new Set(selectable.map((c) => c.id)) : new Set())}
                className="accent-[var(--color-accent)]"
              />
              {t("pages.export.clips")} · {t("pages.export.select_all")}
            </label>
            <Tooltip label={t("pages.export.render_all_hint")} side="bottom-end">
              <button type="button" data-testid="render-all" disabled={selectedIds.length === 0} onClick={() => askRender(selectedIds)} className={primaryBtn}>
                <Clapperboard className="size-4" />
                {t("pages.export.render_all", { count: selectedIds.length })}
              </button>
            </Tooltip>
          </div>
          <ul className="space-y-3">
            {items.map((c) => (
              <ClipRow
                key={c.id}
                clip={c}
                title={titleOf(c)}
                source={sourceName(c.source_id)}
                selected={selected.has(c.id)}
                upload={uploads[c.id]}
                onSelect={(on) =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (on) next.add(c.id);
                    else next.delete(c.id);
                    return next;
                  })
                }
                onRender={() => askRender([c.id])}
                onPlay={(kind) => setPlaying({ clipId: c.id, kind, title: titleOf(c), version: c.files[kind]?.mtime })}
                onOpenFolder={() => void act(() => openClipFolder(c.id), t("pages.export.folder_opened"))}
                onCapcut={() => void act(() => exportCapcut(c.id))}
                onImport={() => startImport(c.id)}
                onEditor={() => navigate(`/editor?clip=${c.id}`)}
              />
            ))}
          </ul>
        </section>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl p-6">
      <PageHeader title={t("pages.export.title")} subtitle={t("pages.export.subtitle")} />
      {body}
      <input ref={fileInput} type="file" accept="video/mp4,video/quicktime,.mp4,.mov,.m4v" className="hidden" data-testid="import-file" onChange={(e) => void onImportFile(e.target.files?.[0])} />
      <ConfirmDialog
        open={confirm !== null}
        title={t("pages.export.rerender_title")}
        confirmLabel={t("pages.export.rerender")}
        danger
        onConfirm={() => confirm && void queueRender(confirm.ids)}
        onCancel={() => setConfirm(null)}
      >
        {confirm && confirm.ids.length === 1 ? t("pages.export.rerender_one_body") : t("pages.export.rerender_body", { count: confirm?.existing ?? 0 })}
      </ConfirmDialog>
      {playing && <PlayerModal {...playing} onClose={() => setPlaying(null)} />}
    </div>
  );
}

function QueueRow({
  clip,
  title,
  source,
  encoder,
  now,
  onCancel,
}: {
  clip: ExportClip;
  title: string;
  source: string;
  encoder: EncoderLabel | null;
  now: number;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation();
  const isRender = isActive(clip.render);
  const task: ExportTask<unknown> = isRender ? clip.render : clip.capcut;
  const left = task.state === "running" ? remainingSeconds(task.started_at, task.progress, now) : null;
  return (
    <li data-testid={`queue-${clip.id}`} className="rounded-lg border border-line bg-panel surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{title}</p>
          <p className="truncate text-xs text-muted">
            {t(isRender ? "jobs.kind_render" : "jobs.kind_capcut")} · {source}
          </p>
        </div>
        <div className="text-xs text-muted">
          {isRender && (
            <span>
              {t("pages.export.renderer")}: {t(`renderer.${clip.render.renderer ?? "remotion"}`)}
              {encoder && (
                <>
                  {" · "}
                  {t("pages.export.encoder")}: {t(`encoder_label.${encoder}`)}
                </>
              )}
            </span>
          )}
        </div>
        <Tooltip label={t("pages.export.cancel_hint")} side="bottom-end">
          <button type="button" data-testid={`cancel-${clip.id}`} onClick={onCancel} className={btn}>
            <X className="size-3.5" />
            {t("pages.export.cancel")}
          </button>
        </Tooltip>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <div className="h-1.5 flex-1 overflow-hidden rounded bg-white/10">
          <div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${Math.round(task.progress * 100)}%` }} />
        </div>
        <span className="w-12 text-right font-mono text-xs tabular-nums">{formatPercent(task.progress, i18n.language)}</span>
      </div>
      <p className="mt-1 text-xs text-muted">
        {task.state === "queued"
          ? t("pages.export.waiting")
          : [
              task.step ? t(`steps.${task.step}`, { defaultValue: task.step }) : null,
              left != null ? t("pages.export.remaining", { time: formatDuration(left) }) : t("pages.export.estimating"),
            ]
              .filter(Boolean)
              .join(" · ")}
      </p>
    </li>
  );
}

function ClipRow({
  clip,
  title,
  source,
  selected,
  upload,
  onSelect,
  onRender,
  onPlay,
  onOpenFolder,
  onCapcut,
  onImport,
  onEditor,
}: {
  clip: ExportClip;
  title: string;
  source: string;
  selected: boolean;
  upload: number | undefined;
  onSelect: (on: boolean) => void;
  onRender: () => void;
  onPlay: (kind: OutputKind) => void;
  onOpenFolder: () => void;
  onCapcut: () => void;
  onImport: () => void;
  onEditor: () => void;
}) {
  const { t, i18n } = useTranslation();
  const info = clip.render.info;
  const final = clip.files.final;
  const isBusy = busy(clip);
  const [showLog, setShowLog] = useState(false);
  // the log of the latest render or CapCut job (whichever ran last)
  const logJob = [clip.capcut, clip.render].filter((x) => x.job_id).sort((a, b) => (b.job_id ?? 0) - (a.job_id ?? 0))[0]?.job_id ?? null;
  const num = (v: number, digits = 1) => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
  const state = clip.render.state === "idle" && final ? "done" : clip.render.state;
  const stateColor =
    state === "done" ? "text-success" : state === "error" ? "text-danger" : state === "running" || state === "queued" ? "text-accent" : "text-muted";
  const warnings = [...(state === "done" ? (info?.warnings ?? []) : []), ...(clip.length_warning && !(info?.warnings ?? []).some((w) => w.code === "CLIP_TOO_LONG") ? [{ code: "CLIP_TOO_LONG", params: clip.length_warning }] : [])];

  return (
    <li data-testid={`export-${clip.id}`} className="flex gap-4 rounded-lg border border-line bg-panel surface p-3">
      <input
        type="checkbox"
        aria-label={t("pages.export.select", { title })}
        checked={selected}
        disabled={isBusy}
        onChange={(e) => onSelect(e.target.checked)}
        className="mt-1 shrink-0 self-start accent-[var(--color-accent)]"
      />
      <button
        type="button"
        aria-label={t("pages.export.play")}
        disabled={!final}
        onClick={() => onPlay("final")}
        className="relative h-[96px] w-[54px] shrink-0 overflow-hidden rounded-md border border-line bg-black disabled:cursor-default"
      >
        {clip.files.thumb ? (
          <img src={outputUrl(clip.id, "thumb", { version: clip.files.thumb.mtime })} alt="" className="size-full object-cover" />
        ) : (
          <Clapperboard className="m-auto size-5 text-muted" />
        )}
        {final && (
          <span className="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 transition-opacity hover:opacity-100">
            <Play className="size-5 text-white" />
          </span>
        )}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <p className="min-w-0 truncate text-sm font-medium">{title}</p>
          <span className={`text-xs ${stateColor}`} data-testid={`state-${clip.id}`}>
            {t(`render_state.${state}`)}
          </span>
          {clip.files.imported && <span className="rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">{t("pages.export.imported")}</span>}
        </div>
        <p className="truncate text-xs text-muted">
          {source} · {formatDuration(clip.duration)} · {t(`platform.${clip.platform ?? "youtube_shorts"}`)}
        </p>
        {state === "done" && info && (
          <p className="mt-1 text-xs text-muted" data-testid={`info-${clip.id}`}>
            {[
              t("pages.export.rendered_at", { when: formatDateTime(info.rendered_at, i18n.language) }),
              `${t(`renderer.${info.renderer}`)} · ${t(`encoder_label.${info.encoder}`)}`,
              info.lufs != null ? t("pages.export.loudness", { lufs: num(info.lufs), peak: num(info.true_peak ?? 0) }) : null,
              info.ssim_min != null ? t("pages.export.ssim", { ssim: num(info.ssim_min, 3) }) : null,
              t("pages.export.render_time", { seconds: num(info.render_seconds, 0) }),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
        {state === "error" && clip.render.error && <p className="mt-1 text-xs text-danger">{translateIssue(t, clip.render.error)}</p>}
        {clip.capcut.state === "error" && clip.capcut.error && <p className="mt-1 text-xs text-danger">{translateIssue(t, clip.capcut.error)}</p>}
        {warnings.map((w, i) => (
          <p key={i} className="mt-1 flex items-start gap-1.5 text-xs text-warning">
            <AlertTriangle className="mt-0.5 size-3 shrink-0" />
            {translateIssue(t, w)}
          </p>
        ))}

        <div className="mt-2 flex flex-wrap gap-1.5">
          <Tooltip label={t("pages.export.render_hint")}>
            <button type="button" data-testid={`render-${clip.id}`} disabled={isBusy} onClick={onRender} className={final ? btn : primaryBtn}>
              {state === "error" || state === "cancelled" ? <RotateCcw className="size-3.5" /> : <Clapperboard className="size-3.5" />}
              {state === "error" || state === "cancelled" ? t("common.retry") : final ? t("pages.export.rerender") : t("pages.export.render")}
            </button>
          </Tooltip>
          {final && (
            <>
              <Tooltip label={t("pages.export.play")}>
                <button type="button" onClick={() => onPlay("final")} className={btn}>
                  <Play className="size-3.5" />
                  {t("pages.export.play")}
                </button>
              </Tooltip>
              <Tooltip label={t("pages.export.download")}>
                <a href={outputUrl(clip.id, "final", { download: true, version: final.mtime })} data-testid={`download-${clip.id}`} className={btn}>
                  <Download className="size-3.5" />
                  {t("pages.export.download")}
                </a>
              </Tooltip>
              {clip.files.srt && (
                <Tooltip label={t("pages.export.srt")}>
                  <a href={outputUrl(clip.id, "srt", { download: true, version: clip.files.srt.mtime })} aria-label={t("pages.export.srt")} className={btn}>
                    <FileText className="size-3.5" />
                    SRT
                  </a>
                </Tooltip>
              )}
              <Tooltip label={t("pages.export.open_folder")}>
                <button type="button" onClick={onOpenFolder} className={btn}>
                  <FolderOpen className="size-3.5" />
                  {t("pages.export.open_folder")}
                </button>
              </Tooltip>
              {clip.capcut.state === "running" || clip.capcut.state === "queued" ? (
                <span className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-muted">
                  <Package className="size-3.5 animate-pulse" />
                  {t("pages.export.capcut_building")}
                </span>
              ) : (
                <Tooltip label={t("pages.export.capcut_hint")}>
                  <button type="button" data-testid={`capcut-${clip.id}`} disabled={isBusy} onClick={onCapcut} className={btn}>
                    <Package className="size-3.5" />
                    {t("pages.export.capcut")}
                  </button>
                </Tooltip>
              )}
              {clip.files.bundle && clip.capcut.state !== "running" && (
                <Tooltip label={t("pages.export.capcut_download")}>
                  <a href={outputUrl(clip.id, "bundle", { download: true, version: clip.files.bundle.mtime })} data-testid={`bundle-${clip.id}`} className={btn}>
                    <FileArchive className="size-3.5" />
                    {t("pages.export.capcut_download")}
                  </a>
                </Tooltip>
              )}
              <Tooltip label={t("pages.export.import_hint")}>
                <button type="button" data-testid={`import-${clip.id}`} disabled={upload !== undefined} onClick={onImport} className={btn}>
                  <Upload className="size-3.5" />
                  {upload !== undefined ? t("pages.export.importing", { percent: formatPercent(upload, i18n.language) }) : t("pages.export.import")}
                </button>
              </Tooltip>
              {clip.files.imported && (
                <>
                  <Tooltip label={t("pages.export.play_imported")}>
                    <button type="button" onClick={() => onPlay("imported")} aria-label={t("pages.export.play_imported")} className={btn}>
                      <Play className="size-3.5 text-accent" />
                      {t("pages.export.imported")}
                    </button>
                  </Tooltip>
                  <Tooltip label={t("pages.export.download_imported")}>
                    <a href={outputUrl(clip.id, "imported", { download: true, version: clip.files.imported.mtime })} aria-label={t("pages.export.download_imported")} className={btn}>
                      <Download className="size-3.5 text-accent" />
                    </a>
                  </Tooltip>
                </>
              )}
            </>
          )}
          {logJob && (
            <Tooltip label={showLog ? t("common.hide_log") : t("common.view_log")}>
              <button type="button" data-testid={`log-${clip.id}`} onClick={() => setShowLog((v) => !v)} aria-expanded={showLog} className={btn}>
                <ScrollText className="size-3.5" />
                {showLog ? t("common.hide_log") : t("common.view_log")}
              </button>
            </Tooltip>
          )}
          <Tooltip label={t("pages.export.open_editor")}>
            <button type="button" onClick={onEditor} aria-label={t("pages.export.open_editor")} className={btn}>
              <Pencil className="size-3.5" />
            </button>
          </Tooltip>
        </div>
        {showLog && logJob && (
          <div className="mt-2" data-testid={`joblog-${clip.id}`}>
            <JobLog jobId={logJob} live={isBusy} />
          </div>
        )}
      </div>
    </li>
  );
}

function PlayerModal({ clipId, kind, title, version, onClose }: { clipId: string; kind: OutputKind; title: string; version?: number; onClose: () => void }) {
  const { t } = useTranslation();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-50 flex items-center justify-center bg-black/80" onClick={onClose}>
      <div className="flex flex-col items-center gap-2" onClick={(e) => e.stopPropagation()}>
        <div className="flex w-full items-center justify-between gap-4">
          <p className="truncate text-sm font-medium">{t("pages.export.player_title", { title })}</p>
          <Tooltip label={t("common.close")} shortcut="Esc" side="bottom-end">
            <button type="button" onClick={onClose} aria-label={t("common.close")} className="rounded p-1 text-muted hover:text-fg">
              <X className="size-4" />
            </button>
          </Tooltip>
        </div>
        <video data-testid="export-player" src={outputUrl(clipId, kind, { version })} controls autoPlay className="h-[82vh] rounded-lg bg-black" style={{ aspectRatio: "9 / 16" }} />
      </div>
    </div>
  );
}
