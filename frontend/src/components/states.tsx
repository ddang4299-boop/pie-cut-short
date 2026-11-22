import { AlertTriangle, RotateCw, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Tooltip } from "./Tooltip";

/** The three screen states from the UI spec: empty, loading (skeleton) and error. */

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  message,
  action,
}: {
  icon: LucideIcon;
  message: string;
  action?: { label: string; onClick: () => void; disabled?: boolean; tooltip?: string };
}) {
  const button = action && (
    <button
      type="button"
      onClick={action.onClick}
      disabled={action.disabled}
      className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {action.label}
    </button>
  );
  return (
    <div data-testid="empty-state" className="flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-line bg-panel/50 px-8 py-16 text-center">
      <Icon className="size-10 text-muted" strokeWidth={1.5} />
      <p className="max-w-lg text-sm text-muted">{message}</p>
      {button && <Tooltip label={action.tooltip ?? action.label}>{button}</Tooltip>}
    </div>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div data-testid="skeleton" className="space-y-3" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-white/5" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry, log }: { message: string; onRetry: () => void; log?: ReactNode }) {
  const { t } = useTranslation();
  const [showLog, setShowLog] = useState(false);
  return (
    <div data-testid="error-state" className="rounded-xl border border-danger/40 bg-danger/5 p-5">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-danger" />
        <p className="flex-1 text-sm">{message}</p>
      </div>
      <div className="mt-4 flex gap-2 pl-8">
        <Tooltip label={t("common.retry")}>
          <button
            type="button"
            onClick={onRetry}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90"
          >
            <RotateCw className="size-3.5" />
            {t("common.retry")}
          </button>
        </Tooltip>
        {log && (
          <Tooltip label={showLog ? t("common.hide_log") : t("common.view_log")}>
            <button
              type="button"
              onClick={() => setShowLog((v) => !v)}
              className="rounded-lg border border-line px-3 py-1.5 text-xs text-fg hover:bg-white/5"
            >
              {showLog ? t("common.hide_log") : t("common.view_log")}
            </button>
          </Tooltip>
        )}
      </div>
      {showLog && log && (
        <pre className="mt-4 ml-8 overflow-x-auto rounded-lg border border-line bg-app p-3 font-mono text-xs text-muted">{log}</pre>
      )}
    </div>
  );
}
