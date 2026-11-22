import { ListChecks } from "lucide-react";
import { useTranslation } from "react-i18next";

import { BackendStatus } from "./BackendStatus";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { Tooltip } from "./Tooltip";

export function Header({ onOpenJobs, runningJobs = 0 }: { onOpenJobs: () => void; runningJobs?: number }) {
  const { t } = useTranslation();
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-panel px-5">
      <div className="min-w-0 flex-1">
        <div className="text-[11px] uppercase tracking-wide text-muted">{t("header.current_source")}</div>
        <div className="truncate text-sm text-muted" data-testid="current-source">
          {t("header.no_source")}
        </div>
      </div>

      <BackendStatus />

      <Tooltip label={t("header.jobs_running", { count: runningJobs })} side="bottom-end">
        <button
          type="button"
          onClick={onOpenJobs}
          data-testid="jobs-button"
          className="flex items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-xs text-fg hover:bg-white/5"
        >
          <ListChecks className="size-4 text-muted" />
          {t("header.jobs")}
          <span
            className={`min-w-5 rounded-full px-1.5 text-center font-mono text-[11px] ${
              runningJobs > 0 ? "bg-accent text-white" : "bg-white/10 text-muted"
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
