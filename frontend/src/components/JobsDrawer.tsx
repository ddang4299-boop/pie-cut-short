import { ListChecks, Square, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, cancelJob, type Job, type JobStatus } from "../lib/api";
import { translateIssue } from "../lib/errors";
import { formatDateTime, formatPercent } from "../lib/format";
import { useSources } from "../state/sources";
import { JobLog } from "./JobLog";
import { useToast } from "./Toasts";
import { Tooltip } from "./Tooltip";

const STATUS_BADGE: Record<JobStatus, string> = {
  queued: "bg-white/10 text-muted",
  running: "bg-accent/15 text-accent",
  done: "bg-success/15 text-success",
  error: "bg-danger/15 text-danger",
  cancelled: "bg-white/10 text-muted",
};

function JobRow({ job }: { job: Job }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? "en";
  const toast = useToast();
  const { refresh } = useSources();
  const [showLog, setShowLog] = useState(false);
  const active = job.status === "queued" || job.status === "running";

  const cancel = async () => {
    try {
      await cancelJob(job.id);
      toast.show(t("jobs.cancelled_toast"), "info");
    } catch (err) {
      toast.show(translateIssue(t, err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} }), "error");
    } finally {
      void refresh();
    }
  };

  return (
    <li className="border-b border-line px-5 py-3" data-testid="job-row" data-status={job.status}>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{t(`jobs.kind_${job.kind}`, { defaultValue: job.kind })}</span>
        <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_BADGE[job.status]}`}>{t(`job_status.${job.status}`)}</span>
        <span className="ml-auto font-mono text-xs text-muted">#{job.id}</span>
      </div>
      <div className="mt-0.5 truncate text-xs text-muted" title={job.source_name ?? undefined}>
        {job.source_name ?? t("jobs.deleted_source")}
      </div>
      {active && (
        <>
          <div className="mt-2 flex items-center justify-between text-xs text-muted">
            <span>{job.step ? t(`steps.${job.step}`, { defaultValue: job.step }) : t("job_status.queued")}</span>
            <span className="font-mono">{formatPercent(job.progress, locale)}</span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/10">
            <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${job.progress * 100}%` }} />
          </div>
        </>
      )}
      {job.status === "error" && job.error && <p className="mt-1.5 text-xs text-danger">{translateIssue(t, job.error)}</p>}
      <div className="mt-2 flex items-center gap-2 text-xs text-muted">
        <span>{formatDateTime(job.started_at ?? job.created_at, locale)}</span>
        {job.attempts > 1 && <span>· {t("jobs.attempt", { count: job.attempts })}</span>}
        <span className="ml-auto flex gap-1.5">
          <Tooltip label={showLog ? t("common.hide_log") : t("common.view_log")} side="top">
            <button
              type="button"
              onClick={() => setShowLog((v) => !v)}
              className="rounded border border-line px-2 py-0.5 hover:bg-white/5 hover:text-fg"
            >
              {showLog ? t("common.hide_log") : t("common.view_log")}
            </button>
          </Tooltip>
          {active && (
            <Tooltip label={t("jobs.cancel")} side="top">
              <button
                type="button"
                onClick={() => void cancel()}
                aria-label={t("jobs.cancel")}
                data-testid="job-cancel"
                className="flex items-center gap-1 rounded border border-line px-2 py-0.5 hover:bg-white/5 hover:text-fg"
              >
                <Square className="size-2.5" />
                {t("common.cancel")}
              </button>
            </Tooltip>
          )}
        </span>
      </div>
      {showLog && (
        <div className="mt-2">
          <JobLog jobId={job.id} live={active} />
        </div>
      )}
    </li>
  );
}

/** Right-side drawer listing background jobs with progress, logs and cancel. */
export function JobsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { jobs } = useSources();
  if (!open) return null;
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const recent = jobs.filter((j) => j.status !== "queued" && j.status !== "running");
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <aside
        role="dialog"
        aria-label={t("jobs.title")}
        data-testid="jobs-drawer"
        className="flex h-full w-[420px] flex-col border-l border-line bg-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-5">
          <h2 className="text-sm font-semibold">{t("jobs.title")}</h2>
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
        {jobs.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-8 text-center">
            <ListChecks className="size-8 text-muted" />
            <p className="text-sm text-muted">{t("jobs.empty")}</p>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {active.length > 0 && (
              <>
                <h3 className="px-5 pt-4 pb-1 text-xs uppercase tracking-wide text-muted">{t("jobs.active")}</h3>
                <ul>
                  {active.map((job) => (
                    <JobRow key={job.id} job={job} />
                  ))}
                </ul>
              </>
            )}
            {recent.length > 0 && (
              <>
                <h3 className="px-5 pt-4 pb-1 text-xs uppercase tracking-wide text-muted">{t("jobs.recent")}</h3>
                <ul>
                  {recent.map((job) => (
                    <JobRow key={job.id} job={job} />
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
