import { Upload } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/states";
import { SystemStatus } from "../components/SystemStatus";
import { Tooltip } from "../components/Tooltip";

/** Library (Phase 0: drop zone placeholder + system status; import arrives in Phase 1). */
export default function Library() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader title={t("pages.library.title")} subtitle={t("pages.library.subtitle")} />

      <div
        data-testid="empty-state"
        className="mb-6 flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-line bg-panel/50 px-8 py-14 text-center"
      >
        <Upload className="size-10 text-muted" strokeWidth={1.5} />
        <p className="text-base font-medium">{t("pages.library.drop")}</p>
        <p className="text-sm text-muted">{t("pages.library.drop_hint")}</p>
        <Tooltip label={t("common.coming_soon")}>
          <button
            type="button"
            disabled
            className="mt-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t("pages.library.choose_file")}
          </button>
        </Tooltip>
      </div>

      <SystemStatus />
    </div>
  );
}
