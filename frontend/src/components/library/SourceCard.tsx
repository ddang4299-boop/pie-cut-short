import { AlertTriangle, ChevronDown, Copy, FileText, Film, Hourglass, Link2, MoreHorizontal, Play, RotateCw, Sparkles, Square, Trash2, WandSparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import {
  ApiError,
  CONTENT_TYPES,
  LANGUAGE_OPTIONS,
  RERUNNABLE_STEPS,
  STEPS,
  apiUrl,
  cancelJob,
  deleteSource,
  findMoments,
  importMomentsNow,
  patchSource,
  rerunSource,
  startSource,
  type ContentType,
  type Job,
  type LanguageOption,
  type Source,
  type Step,
} from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { contentLanguageLabel } from "../../lib/labels";
import { formatBytes, formatDuration, formatPercent, formatResolution } from "../../lib/format";
import { useSources } from "../../state/sources";
import { ConfirmDialog } from "../ConfirmDialog";
import { JobLog } from "../JobLog";
import { Menu } from "../Menu";
import { useToast } from "../Toasts";
import { Tooltip } from "../Tooltip";
import { PreflightSummary } from "./PreflightSummary";
import { StepStepper } from "./StepStepper";

// Dot color for the status badge drawn over the thumbnail (badge itself is always dark for contrast).
const STATUS_DOT: Record<Source["status"], string> = {
  downloading: "bg-accent animate-pulse",
  new: "bg-white",
  processing: "bg-accent animate-pulse",
  waiting: "bg-warning animate-pulse",
  ready: "bg-success",
  error: "bg-danger",
};

/** One source in the Library grid: thumbnail, facts, stepper, preflight, Start panel and actions. */
export function SourceCard({ source, lastJob, onOpenTranscript }: { source: Source; lastJob?: Job; onOpenTranscript: () => void }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? "en";
  const navigate = useNavigate();
  const toast = useToast();
  const { refresh, replaceSource, setCurrentSourceId } = useSources();

  const [language, setLanguage] = useState<LanguageOption>(source.language_override);
  const [notes, setNotes] = useState(source.notes);
  const [busy, setBusy] = useState(false);
  const [rerunStep, setRerunStep] = useState<Step | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteMedia, setDeleteMedia] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [thumbFailed, setThumbFailed] = useState(false);

  const fail = (err: unknown) => {
    const issue = err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} };
    toast.show(translateIssue(t, issue), "error");
  };

  const run = async (action: () => Promise<Source | void>, success?: string) => {
    setBusy(true);
    try {
      const result = await action();
      if (result) replaceSource(result);
      if (success) toast.show(success, "success");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const job = source.active_job;
  const isBusy = job !== null || source.status === "downloading" || source.status === "processing";
  const runningStep = STEPS.find((s) => source.steps[s]?.state === "running");
  const stage = runningStep ? source.steps[runningStep]?.stage : null;
  const preflight = source.preflight;
  const showStart = source.can_start && source.status === "new";
  const showRetry = source.can_start && source.status === "error";
  const logJob = job ?? lastJob;
  const thumb = source.thumbnail_url && !thumbFailed ? apiUrl(source.thumbnail_url) : null;

  const start = () =>
    run(async () => {
      const result = await startSource(source.id, { language, notes });
      setCurrentSourceId(source.id);
      return result;
    }, t("pages.library.started", { name: source.name }));

  const copyCommand = async () => {
    try {
      await navigator.clipboard.writeText(source.find_moments_command);
      toast.show(t("pages.library.command_copied"), "success");
    } catch {
      toast.show(t("pages.library.command_copy_failed", { command: source.find_moments_command }), "error");
    }
  };

  const openMoments = () => {
    setCurrentSourceId(source.id);
    navigate("/moments");
  };

  const typeName = source.content_type ? t(`content_type.${source.content_type}`) : t("content_type.unknown");
  const typeLabel =
    source.content_type && !source.content_type_override ? t("pages.library.content_type_auto", { type: typeName }) : typeName;
  const momentsStep = source.steps.moments;

  return (
    <article
      data-testid="source-card"
      data-source-id={source.id}
      data-status={source.status}
      className="flex flex-col rounded-xl border border-line bg-panel surface"
    >
      {/* Thumbnail */}
      <div className="relative aspect-video overflow-hidden rounded-t-xl bg-app">
        {thumb ? (
          <img src={thumb} alt="" className="size-full object-contain" onError={() => setThumbFailed(true)} />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-2 text-xs text-muted">
            <Film className="size-8" strokeWidth={1.25} />
            {t("pages.library.no_thumbnail")}
          </div>
        )}
        <span className="absolute top-2 left-2 flex items-center gap-1.5 rounded-md bg-black/80 px-2 py-0.5 text-[11px] font-medium text-white">
          <span className={`size-1.5 rounded-full ${STATUS_DOT[source.status]}`} />
          {t(`source_status.${source.status}`)}
        </span>
        {source.duration != null && (
          <Tooltip label={t("pages.library.duration")} side="top">
            <span className="absolute right-2 bottom-2 rounded bg-black/75 px-1.5 py-0.5 font-mono text-[11px] text-white">
              {formatDuration(source.duration)}
            </span>
          </Tooltip>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-3 p-4">
        {/* Title + menu */}
        <div className="flex items-start gap-2">
          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold" title={source.name} data-testid="source-name">
            {source.name}
          </h3>
          <Menu
            testId="source-menu"
            trigger={({ toggle }) => (
              <Tooltip label={t("common.more")} side="bottom-end">
                <button
                  type="button"
                  onClick={toggle}
                  aria-label={t("common.more")}
                  data-testid="source-menu-button"
                  className="rounded-md p-1 text-muted hover:bg-white/5 hover:text-fg"
                >
                  <MoreHorizontal className="size-4" />
                </button>
              </Tooltip>
            )}
            sections={[
              {
                key: "open",
                items: [
                  {
                    key: "moments",
                    label: t("pages.library.open_moments"),
                    onSelect: openMoments,
                    disabled: !source.has_transcript && source.status !== "ready",
                  },
                  {
                    key: "transcript",
                    label: t("pages.library.view_transcript"),
                    icon: <FileText className="size-3.5" />,
                    onSelect: onOpenTranscript,
                    disabled: !source.has_transcript,
                  },
                ],
              },
              {
                key: "rerun",
                title: t("pages.library.rerun_section"),
                items: RERUNNABLE_STEPS.map((step) => ({
                  key: step,
                  label: t(`steps.${step}`),
                  icon: <RotateCw className="size-3.5" />,
                  onSelect: () => setRerunStep(step),
                  disabled: isBusy || !source.media_path || (step !== "preflight" && !preflight?.ok),
                })),
              },
              {
                key: "danger",
                items: [
                  {
                    key: "delete",
                    label: t("pages.library.delete"),
                    icon: <Trash2 className="size-3.5" />,
                    danger: true,
                    onSelect: () => {
                      setDeleteMedia(false);
                      setDeleteOpen(true);
                    },
                  },
                ],
              },
            ]}
          />
        </div>

        {/* Facts */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <Tooltip label={t("pages.library.resolution")} side="top">
            <span className="font-mono">{formatResolution(source.width, source.height)}</span>
          </Tooltip>
          {source.fps != null && <span>{t("pages.library.fps", { value: source.fps })}</span>}
          <Tooltip label={t("pages.library.file_size")} side="top">
            <span>{formatBytes(source.size_bytes, locale)}</span>
          </Tooltip>
          {source.language && (
            <Tooltip label={t("pages.library.language_detected")} side="top">
              <span>{contentLanguageLabel(t, source.language)}</span>
            </Tooltip>
          )}
          {source.origin === "url" && source.url && (
            <Tooltip label={source.url} side="top">
              <Link2 className="size-3.5" />
            </Tooltip>
          )}
        </div>

        {/* Content type badge (click to change) */}
        <Menu
          align="left"
          testId="content-type-menu"
          trigger={({ toggle }) => (
            <Tooltip label={t("pages.library.content_type_hint")} side="top">
              <button
                type="button"
                onClick={toggle}
                data-testid="content-type-badge"
                className={`flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] hover:bg-white/5 ${
                  source.content_type ? "border-accent/40 text-fg" : "border-line text-muted"
                }`}
              >
                {typeLabel}
                <ChevronDown className="size-3" />
              </button>
            </Tooltip>
          )}
          sections={[
            {
              key: "types",
              items: [
                ...CONTENT_TYPES.map((type: ContentType) => ({
                  key: type,
                  label: t(`content_type.${type}`),
                  onSelect: () => void run(() => patchSource(source.id, { content_type: type })),
                  icon: source.content_type_override === type ? <span className="size-1.5 rounded-full bg-accent" /> : undefined,
                })),
                {
                  key: "auto",
                  label: t("content_type.auto"),
                  onSelect: () => void run(() => patchSource(source.id, { content_type: null })),
                },
              ],
            },
          ]}
        />

        <StepStepper source={source} />

        {/* Running job */}
        {job && (
          <div className="rounded-lg border border-line bg-app/60 px-3 py-2" data-testid="active-job">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="min-w-0 truncate text-fg/90">
                {stage ? t(`stage.${stage}`, { defaultValue: stage }) : t(`steps.${job.step ?? "preflight"}`, { defaultValue: job.step ?? "" })}
                {job.status === "queued" && ` · ${t("job_status.queued")}`}
              </span>
              <span className="font-mono text-muted">{formatPercent(job.progress, locale)}</span>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${job.progress * 100}%` }} />
            </div>
          </div>
        )}

        {/* Waiting for Claude Code (/find-moments) */}
        {source.status === "waiting" && (
          <div className="space-y-2 rounded-lg border border-warning/40 bg-warning/5 p-3" data-testid="waiting-panel">
            <div className="flex items-center gap-2 text-xs font-medium text-warning">
              <Hourglass className="size-3.5 shrink-0" />
              {t("pages.library.waiting_title")}
            </div>
            <p className="text-[11px] leading-relaxed text-muted">{t("pages.library.waiting_body")}</p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md border border-line bg-app px-2 py-1 font-mono text-[11px] text-fg" data-testid="find-command">
                {source.find_moments_command}
              </code>
              <Tooltip label={t("pages.library.copy_command")} side="top">
                <button
                  type="button"
                  onClick={() => void copyCommand()}
                  data-testid="copy-command"
                  className="flex shrink-0 items-center gap-1.5 rounded-md btn-primary px-2.5 py-1 text-[11px]"
                >
                  <Copy className="size-3" />
                  {t("pages.library.copy_command")}
                </button>
              </Tooltip>
            </div>
            {momentsStep?.error && (
              <div className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/5 px-2 py-1.5 text-[11px]" data-testid="moments-error">
                <AlertTriangle className="mt-0.5 size-3 shrink-0 text-danger" />
                <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{translateIssue(t, momentsStep.error)}</span>
              </div>
            )}
            <div className="flex flex-wrap gap-x-3 gap-y-1 pt-0.5">
              <Tooltip label={t("pages.library.check_now_hint")} side="top">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(() => importMomentsNow(source.id))}
                  className="text-[11px] text-muted underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
                >
                  {t("pages.library.check_now")}
                </button>
              </Tooltip>
              <Tooltip label={t("pages.library.find_offline_hint")} side="top">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(() => findMoments(source.id, "heuristic"), t("pages.library.rerun_started"))}
                  className="flex items-center gap-1 text-[11px] text-muted underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
                >
                  <WandSparkles className="size-3" />
                  {t("pages.library.find_offline")}
                </button>
              </Tooltip>
            </div>
          </div>
        )}

        {/* Preflight result (until processing has started) */}
        {preflight && (source.status === "new" || preflight.issues.length > 0 || !preflight.ok) && source.status !== "processing" && (
          <PreflightSummary result={preflight} />
        )}

        {/* Source-level error */}
        {source.status === "error" && source.error && (
          <div className="flex items-start gap-2 rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-xs" data-testid="source-error">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-danger" />
            <span className="flex-1">{translateIssue(t, source.error)}</span>
          </div>
        )}
        {source.status !== "error" && source.error?.code === "JOB_CANCELLED" && (
          <p className="text-xs text-muted">{translateIssue(t, source.error)}</p>
        )}

        {/* Start panel */}
        {showStart && (
          <div className="space-y-2.5 rounded-lg border border-line bg-app/60 p-3" data-testid="start-panel">
            <div className="text-xs font-medium">{t("pages.library.start_title")}</div>
            <label className="block text-[11px] text-muted">
              {t("pages.library.language_label")}
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value as LanguageOption)}
                data-testid="language-select"
                className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-xs text-fg outline-none focus:border-accent"
              >
                {LANGUAGE_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {t(`language_option.${option}`)}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-[11px] text-muted">
              {t("pages.library.notes_label")}
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                placeholder={t("pages.library.notes_placeholder")}
                className="mt-1 w-full resize-none rounded-md border border-line bg-panel px-2 py-1.5 text-xs text-fg outline-none placeholder:text-muted/60 focus:border-accent"
              />
            </label>
            <Tooltip label={t("pages.library.start_title")} side="top">
              <button
                type="button"
                onClick={() => void start()}
                disabled={busy}
                data-testid="start-button"
                className="flex items-center gap-1.5 rounded-lg btn-primary px-4 py-1.5 text-xs disabled:opacity-50"
              >
                <Play className="size-3.5" />
                {t("common.start")}
              </button>
            </Tooltip>
          </div>
        )}

        {/* Footer actions */}
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          {showRetry && (
            <Tooltip label={t("pages.library.retry")} side="top">
              <button
                type="button"
                disabled={busy}
                onClick={() => void start()}
                data-testid="retry-button"
                className="flex items-center gap-1.5 rounded-lg btn-primary px-3 py-1.5 text-xs disabled:opacity-50"
              >
                <RotateCw className="size-3.5" />
                {t("common.retry")}
              </button>
            </Tooltip>
          )}
          {job && (
            <Tooltip label={t("pages.library.cancel_job")} side="top">
              <button
                type="button"
                disabled={busy}
                onClick={() => void run(async () => void (await cancelJob(job.id)), t("jobs.cancelled_toast"))}
                data-testid="cancel-job-button"
                className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:bg-white/5 disabled:opacity-50"
              >
                <Square className="size-3" />
                {t("common.cancel")}
              </button>
            </Tooltip>
          )}
          {(source.status === "ready" || source.status === "waiting") && (
            <Tooltip label={t("pages.library.open_moments")} side="top">
              <button
                type="button"
                onClick={openMoments}
                data-testid="open-moments"
                className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                  source.status === "ready" ? "btn-primary" : "border border-line text-fg hover:bg-white/5"
                }`}
              >
                {t("pages.library.open_moments")}
              </button>
            </Tooltip>
          )}
          {source.has_transcript && (
            <Tooltip label={t("pages.library.view_transcript")} side="top">
              <button
                type="button"
                onClick={onOpenTranscript}
                data-testid="transcript-button"
                className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:bg-white/5"
              >
                <FileText className="size-3.5" />
                {t("pages.library.view_transcript")}
              </button>
            </Tooltip>
          )}
          {logJob && (source.status === "error" || job) && (
            <Tooltip label={showLog ? t("common.hide_log") : t("common.view_log")} side="top">
              <button
                type="button"
                onClick={() => setShowLog((v) => !v)}
                className="rounded-lg border border-line px-3 py-1.5 text-xs text-muted hover:bg-white/5 hover:text-fg"
              >
                {showLog ? t("common.hide_log") : t("common.view_log")}
              </button>
            </Tooltip>
          )}
          <span className="ml-auto flex items-center gap-1 text-[11px] text-muted" data-testid="moments-count">
            <Sparkles className="size-3" />
            {t("pages.library.moments_count", { approved: source.moments.approved, total: source.moments.total })}
          </span>
        </div>
        {showLog && logJob && <JobLog jobId={logJob.id} live={job !== null} />}
      </div>

      <ConfirmDialog
        open={rerunStep !== null}
        title={t("pages.library.rerun_title", { step: rerunStep ? t(`steps.${rerunStep}`) : "" })}
        confirmLabel={t("pages.library.rerun_confirm")}
        busy={busy}
        onCancel={() => setRerunStep(null)}
        onConfirm={() => {
          const step = rerunStep;
          setRerunStep(null);
          if (step) void run(() => rerunSource(source.id, step), t("pages.library.rerun_started"));
        }}
      >
        <p>{t("pages.library.rerun_body")}</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteOpen}
        danger
        title={t("pages.library.delete_title", { name: source.name })}
        confirmLabel={t("common.delete")}
        busy={busy}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => {
          setDeleteOpen(false);
          void run(async () => {
            await deleteSource(source.id, deleteMedia);
          }, t("pages.library.deleted"));
        }}
      >
        <p>{t("pages.library.delete_body")}</p>
        <label className="flex items-start gap-2 text-fg">
          <input
            type="checkbox"
            checked={deleteMedia}
            onChange={(e) => setDeleteMedia(e.target.checked)}
            data-testid="delete-media-checkbox"
            className="mt-0.5 accent-[#7c5cff]"
          />
          <span>{t("pages.library.delete_media")}</span>
        </label>
        {!source.owns_media && source.media_path && (
          <p className="text-xs break-all">{t("pages.library.delete_media_external", { path: source.media_path })}</p>
        )}
      </ConfirmDialog>
    </article>
  );
}
