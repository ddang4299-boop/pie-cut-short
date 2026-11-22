import { ListChecks, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Tooltip } from "./Tooltip";

/** Right-side drawer listing background jobs (filled in Phase 1). */
export function JobsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <aside
        role="dialog"
        aria-label={t("jobs.title")}
        data-testid="jobs-drawer"
        className="flex h-full w-96 flex-col border-l border-line bg-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex h-14 items-center justify-between border-b border-line px-5">
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
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-8 text-center">
          <ListChecks className="size-8 text-muted" />
          <p className="text-sm text-muted">{t("jobs.empty")}</p>
        </div>
      </aside>
    </div>
  );
}
