import { AlertTriangle, Check, ListChecks, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSaveStatus } from "../state/save";
import { useSources } from "../state/sources";
import { BackendStatus } from "./BackendStatus";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { Tooltip } from "./Tooltip";

export function Header({ onOpenJobs }: { onOpenJobs: () => void }) {
  const { t } = useTranslation();
  const { currentSource, activeJobCount: runningJobs } = useSources();
  const { status: saveStatus } = useSaveStatus();
  return (
    <header className="relative z-20 flex h-14 shrink-0 items-center gap-4 border-b border-line bg-black/70 px-5 backdrop-blur-md backdrop-saturate-150">
      <span className="font-serif text-[26px] leading-none tracking-tight text-gradient" data-testid="app-wordmark">
        {t("app.name")}
      </span>
      <span className="h-6 w-px bg-line" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-xs uppercase tracking-wide text-subtle">{t("header.current_source")}</div>
        <div
          className={`truncate text-sm ${currentSource ? "text-fg" : "text-muted"}`}
          data-testid="current-source"
          title={currentSource?.name}
        >
          {currentSource ? currentSource.name : t("header.no_source")}
        </div>
      </div>

      {saveStatus !== "idle" && (
        <span
          data-testid="save-status"
          data-status={saveStatus}
          className={`flex items-center gap-1.5 text-xs ${saveStatus === "error" ? "text-danger" : "text-muted"}`}
        >
          {saveStatus === "saving" && <Loader2 className="size-3.5 animate-spin" />}
          {saveStatus === "saved" && <Check className="size-3.5 text-success" />}
          {saveStatus === "error" && <AlertTriangle className="size-3.5" />}
          {t(`header.save_${saveStatus}`)}
        </span>
      )}

      <BackendStatus />

      <Tooltip label={t("header.jobs_running", { count: runningJobs })} side="bottom-end">
        <button
          type="button"
          onClick={onOpenJobs}
          data-testid="jobs-button"
          className="flex items-center gap-2 rounded-full border border-line bg-white/[0.04] px-3 py-1.5 text-xs text-fg transition-colors hover:border-line-strong hover:bg-white/[0.07]"
        >
          <ListChecks className="size-4 text-muted" />
          {t("header.jobs")}
          <span
            className={`min-w-5 rounded-full px-1.5 text-center font-mono text-xs ${
              runningJobs > 0 ? "bg-accent text-white shadow-[0_0_12px_rgba(124,92,255,0.6)]" : "bg-white/10 text-muted"
            }`}
          >
            {runningJobs}
          </span>
        </button>
      </Tooltip>

      <LanguageSwitcher />
    </header>
  );
}
