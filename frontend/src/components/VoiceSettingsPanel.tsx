import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, VOICE_MODELS, getSettings, listVoices, patchSettings, type AppSettings, type Voice, type VoiceModel } from "../lib/api";
import { translateIssue } from "../lib/errors";
import { VoiceStatusNote, useVoiceStatus } from "./editor/VoiceTab";
import { useToast } from "./Toasts";
import { Tooltip } from "./Tooltip";

const selectCls = "w-full min-w-0 rounded-md border border-line bg-app px-2 py-1.5 text-sm outline-none focus:border-accent";

/** Settings → ElevenLabs: key status, usage, default voice and model. Never spends credits. */
export function VoiceSettingsPanel() {
  const { t } = useTranslation();
  const toast = useToast();
  const { status, refresh } = useVoiceStatus();
  const [prefs, setPrefs] = useState<AppSettings | null>(null);
  const [voices, setVoices] = useState<Voice[] | null>(null);

  useEffect(() => {
    getSettings().then(setPrefs, () => setPrefs(null));
  }, []);
  useEffect(() => {
    if (!status?.configured) return;
    listVoices().then(setVoices, () => setVoices(null));
  }, [status?.configured]);

  const save = async (patch: { voice_default_id?: string; voice_model?: VoiceModel }) => {
    try {
      setPrefs(await patchSettings(patch));
      toast.show(t("pages.settings.saved"), "success");
    } catch (err) {
      toast.show(translateIssue(t, err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} }), "error");
    }
  };

  return (
    <section className="rounded-xl border border-line bg-panel surface p-5" data-testid="voice-settings">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{t("pages.settings.voice_title")}</h2>
          <p className="mt-1 text-sm text-muted">{t("pages.settings.voice_hint")}</p>
        </div>
        <Tooltip label={t("pages.settings.voice_refresh")} side="bottom">
          <button
            type="button"
            aria-label={t("pages.settings.voice_refresh")}
            onClick={() => refresh(true)}
            className="flex size-8 shrink-0 items-center justify-center rounded-md border border-line text-muted hover:bg-white/5 hover:text-fg"
          >
            <RefreshCw className="size-4" />
          </button>
        </Tooltip>
      </div>
      <div className="mt-4 space-y-4">
        <div className="flex items-center gap-2 text-sm" data-testid="voice-key-status">
          <span className={`size-2 rounded-full ${status?.configured ? "bg-success" : "bg-warning"}`} />
          {status ? (status.mock ? t("pages.settings.voice_key_mock") : status.configured ? t("pages.settings.voice_key_ok") : t("pages.settings.voice_key_missing")) : "…"}
        </div>
        <VoiceStatusNote status={status} />
        {prefs && (
          <div className="grid grid-cols-2 gap-4">
            <label className="min-w-0 text-sm">
              <span className="mb-1 block text-muted">{t("pages.settings.voice_default")}</span>
              <select
                className={selectCls}
                value={prefs.voice_default_id}
                disabled={!voices?.length}
                data-testid="settings-voice"
                onChange={(e) => void save({ voice_default_id: e.target.value })}
              >
                <option value="">{t("pages.settings.voice_default_first")}</option>
                {voices?.map((v) => (
                  <option key={v.voice_id} value={v.voice_id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="min-w-0 text-sm">
              <span className="mb-1 block text-muted">{t("pages.settings.voice_model")}</span>
              <select className={selectCls} value={prefs.voice_model} data-testid="settings-voice-model" onChange={(e) => void save({ voice_model: e.target.value as VoiceModel })}>
                {VOICE_MODELS.map((m) => (
                  <option key={m} value={m}>
                    {t(`pages.editor.voice_model_name.${m}`)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        <p className="text-xs text-muted">{t("pages.settings.voice_credits_note")}</p>
      </div>
    </section>
  );
}
