import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { PageHeader } from "../components/states";
import { SystemStatus } from "../components/SystemStatus";

/** Settings (Phase 0: UI language + system status; the rest arrives in Phase 7). */
export default function Settings() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <PageHeader title={t("pages.settings.title")} subtitle={t("pages.settings.subtitle")} />

      <section className="rounded-xl border border-line bg-panel p-5">
        <h2 className="text-sm font-semibold">{t("pages.settings.language_title")}</h2>
        <p className="mt-1 mb-4 text-sm text-muted">{t("pages.settings.language_hint")}</p>
        <LanguageSwitcher variant="full" />
      </section>

      <div>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{t("pages.settings.system_title")}</h2>
        <SystemStatus />
      </div>
    </div>
  );
}
