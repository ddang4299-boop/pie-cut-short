import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { CachePanel } from "../components/settings/CachePanel";
import { ChannelsPanel } from "../components/settings/ChannelsPanel";
import { AiSection, PresetsSection, RenderSection, TranscriptionSection } from "../components/settings/GeneralSections";
import { ErrorState, PageHeader, Skeleton } from "../components/states";
import { SystemStatus } from "../components/SystemStatus";
import { useToast } from "../components/Toasts";
import { VoiceSettingsPanel } from "../components/VoiceSettingsPanel";
import { ApiError, getSettings, patchSettings, type AppSettings } from "../lib/api";
import { translateIssue } from "../lib/errors";

/** Settings: every preference of the app (stored in SQLite), saved as soon as a field changes. */
export default function Settings() {
  const { t } = useTranslation();
  const toast = useToast();
  const [s, setS] = useState<AppSettings | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    setFailed(false);
    getSettings().then(setS, () => setFailed(true));
  }, []);
  useEffect(load, [load]);

  const save = useCallback(
    async (patch: Partial<AppSettings>) => {
      try {
        setS(await patchSettings(patch));
        toast.show(t("pages.settings.saved"), "success");
      } catch (err) {
        toast.show(translateIssue(t, err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} }), "error");
        load(); // show the stored values again
      }
    },
    [load, t, toast],
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <PageHeader title={t("pages.settings.title")} subtitle={t("pages.settings.subtitle")} />

      <section className="rounded-xl border border-line bg-panel surface p-5">
        <h2 className="text-sm font-semibold">{t("pages.settings.language_title")}</h2>
        <p className="mt-1 mb-4 text-sm text-muted">{t("pages.settings.language_hint")}</p>
        <LanguageSwitcher variant="full" />
      </section>

      {failed && <ErrorState message={t("pages.settings.load_failed")} onRetry={load} />}
      {!s && !failed && <Skeleton rows={6} />}
      {s && (
        <>
          <AiSection s={s} save={save} />
          <TranscriptionSection s={s} save={save} />
          <RenderSection s={s} save={save} />
          <PresetsSection s={s} save={save} />
          <ChannelsPanel s={s} save={save} />
        </>
      )}

      <VoiceSettingsPanel />
      <CachePanel />

      <div>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{t("pages.settings.system_title")}</h2>
        <SystemStatus />
      </div>
    </div>
  );
}
