import { CheckCircle2, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { Severity } from "../lib/api";
import { translateIssue } from "../lib/errors";
import { useHealth } from "../state/health";
import { LEVEL_DOT } from "./BackendStatus";
import { ErrorState, Skeleton } from "./states";
import { Tooltip } from "./Tooltip";

export const START_COMMAND = "cd backend && uvicorn app.main:app --reload";

export const SEVERITY_BADGE: Record<Severity, string> = {
  error: "bg-danger/15 text-danger",
  warning: "bg-warning/15 text-warning",
  info: "bg-white/10 text-muted",
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 truncate">{children}</dd>
    </>
  );
}

/** Backend /health details: ffmpeg, device, GPU, encoder and translated notices. */
export function SystemStatus() {
  const { t } = useTranslation();
  const { data, error, loading, level, refresh } = useHealth();

  const header = (
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-sm font-semibold">{t("system.title")}</h2>
      <Tooltip label={t("system.check_again")} side="bottom-end">
        <button
          type="button"
          onClick={refresh}
          aria-label={t("system.check_again")}
          className="rounded-md p-1.5 text-muted hover:bg-white/5 hover:text-fg"
        >
          <RotateCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </Tooltip>
    </div>
  );

  let body: ReactNode;
  if (error) {
    body = (
      <ErrorState
        message={translateIssue(t, error.issue)}
        onRetry={refresh}
        log={`${error.issue.code} (HTTP ${error.status || "-"})\n\n${t("system.start_hint")}\n  ${START_COMMAND}`}
      />
    );
  } else if (!data) {
    body = <Skeleton rows={5} />;
  } else {
    const gpu = data.gpu
      ? data.gpu.memory_mb
        ? `${data.gpu.name} · ${t("system.memory_gb", { value: data.gpu.memory_mb / 1024 })}`
        : data.gpu.name
      : t("system.none");
    body = (
      <>
        <dl className="grid grid-cols-[minmax(10rem,auto)_1fr] gap-x-6 gap-y-2 text-sm">
          <Row label={t("system.backend")}>
            <span className="inline-flex items-center gap-2" data-testid="system-backend">
              <span className={`size-2 rounded-full ${LEVEL_DOT[level]}`} />
              {t(`backend.${level}`)}
            </span>
          </Row>
          <Row label={t("system.ffmpeg")}>
            {data.ffmpeg.available ? (
              <span className="font-mono text-xs">{data.ffmpeg.version}</span>
            ) : (
              <span className="text-danger">{t("system.not_found")}</span>
            )}
          </Row>
          <Row label={t("system.device")}>{t(`device.${data.device}`)}</Row>
          <Row label={t("system.gpu")}>{gpu}</Row>
          <Row label={t("system.encoder")}>{t(`encoder.${data.encoder}`, { defaultValue: data.encoder })}</Row>
          <Row label={t("system.python")}>
            <span className="font-mono text-xs">{data.python}</span>
          </Row>
          <Row label={t("system.app_version")}>
            <span className="font-mono text-xs">{data.app_version}</span>
          </Row>
        </dl>

        <h3 className="mt-5 mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{t("system.notices")}</h3>
        {data.issues.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-success">
            <CheckCircle2 className="size-4" />
            {t("system.no_notices")}
          </p>
        ) : (
          <ul className="space-y-2">
            {data.issues.map((issue) => (
              <li key={issue.code} className="flex items-start gap-2 text-sm">
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${SEVERITY_BADGE[issue.severity]}`}>
                  {t(`severity.${issue.severity}`)}
                </span>
                <span>{translateIssue(t, issue)}</span>
              </li>
            ))}
          </ul>
        )}
      </>
    );
  }

  return (
    <section data-testid="system-status" className="rounded-xl border border-line bg-panel surface p-5">
      {header}
      {body}
    </section>
  );
}
