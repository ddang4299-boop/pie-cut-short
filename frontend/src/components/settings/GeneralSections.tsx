import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  CAPTION_STYLES,
  LLM_PROVIDERS,
  PLATFORMS,
  RENDERERS,
  RUBRIC,
  getRenderPresets,
  type AppSettings,
  type CaptionStyle,
  type Device,
  type LlmProvider,
  type Platform,
  type PresetOverride,
  type RenderPresets,
  type Renderer,
} from "../../lib/api";
import { Tooltip } from "../Tooltip";
import { Card, Field, NumberInput, TextInput, selectCls } from "./Controls";

export type SaveFn = (patch: Partial<AppSettings>) => Promise<void>;

const DEFAULT_WEIGHTS = { hook_strength: 0.3, coherence: 0.2, emotion: 0.2, value_density: 0.15, payoff: 0.15 };

function ResetButton({ label, onClick, testId }: { label: string; onClick: () => void; testId?: string }) {
  return (
    <Tooltip label={label} side="top">
      <button type="button" aria-label={label} data-testid={testId} onClick={onClick}
        className="flex size-8 items-center justify-center rounded-md border border-line text-muted hover:bg-white/5 hover:text-fg">
        <RotateCcw className="size-4" />
      </button>
    </Tooltip>
  );
}

/** AI moment selection: provider, rubric weights, clip length, how many moments. */
export function AiSection({ s, save }: { s: AppSettings; save: SaveFn }) {
  const { t, i18n } = useTranslation();
  const total = RUBRIC.reduce((sum, k) => sum + (s.rubric_weights[k] ?? 0), 0);
  const keyState = (p: LlmProvider) =>
    p === "anthropic" ? s.env.anthropic_configured : p === "openai" ? s.env.openai_configured : true;
  const nf = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 });
  return (
    <Card title={t("pages.settings.ai_title")} hint={t("pages.settings.ai_hint")} testId="settings-ai">
      <Field label={t("pages.settings.llm_provider")} hint={t(`llm_provider_hint.${s.effective_llm_provider}`)}>
        <select className={selectCls} data-testid="llm-provider" value={s.llm_provider ?? ""}
          onChange={(e) => void save({ llm_provider: (e.target.value || null) as LlmProvider | null })}>
          <option value="">{t("pages.settings.from_env", { value: t(`llm_provider.${s.env.llm_provider}`, { defaultValue: s.env.llm_provider }) })}</option>
          {LLM_PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {t(`llm_provider.${p}`)}
              {keyState(p) ? "" : ` · ${t("pages.settings.no_key")}`}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("pages.settings.rubric_weights")} hint={t("pages.settings.rubric_hint", { total: nf.format(total) })}>
        <div className="flex flex-wrap items-end gap-3">
          {RUBRIC.map((k) => (
            <label key={k} className="flex flex-col gap-1 text-xs text-muted">
              {t(`rubric.${k}`)}
              <NumberInput label={t(`rubric.${k}`)} testId={`weight-${k}`} value={s.rubric_weights[k]} min={0} max={1} step={0.05}
                onCommit={(v) => void save({ rubric_weights: { ...s.rubric_weights, [k]: v } })} className="w-20" />
            </label>
          ))}
          <ResetButton label={t("pages.settings.reset_defaults")} testId="weights-reset" onClick={() => void save({ rubric_weights: { ...DEFAULT_WEIGHTS } })} />
        </div>
      </Field>
      <Field label={t("pages.settings.clip_length")} hint={t("pages.settings.clip_length_hint")}>
        <div className="flex items-center gap-2 text-sm">
          <NumberInput label={t("pages.settings.min_seconds")} testId="clip-min" value={s.clip_min} min={3} max={600} onCommit={(v) => void save({ clip_min: v })} />
          <span className="text-muted">–</span>
          <NumberInput label={t("pages.settings.max_seconds")} testId="clip-max" value={s.clip_max} min={3} max={600} onCommit={(v) => void save({ clip_max: v })} />
          <span className="text-muted">{t("pages.settings.seconds")}</span>
        </div>
      </Field>
      <Field label={t("pages.settings.moments_wanted")}>
        <div className="flex items-center gap-2 text-sm">
          <NumberInput label={t("pages.settings.min_count")} testId="wanted-min" value={s.moments_wanted[0]} min={1} max={50}
            onCommit={(v) => void save({ moments_wanted: [Math.round(v), Math.max(Math.round(v), s.moments_wanted[1])] })} />
          <span className="text-muted">–</span>
          <NumberInput label={t("pages.settings.max_count")} testId="wanted-max" value={s.moments_wanted[1]} min={1} max={50}
            onCommit={(v) => void save({ moments_wanted: [Math.min(Math.round(v), s.moments_wanted[0]), Math.round(v)] })} />
        </div>
      </Field>
    </Card>
  );
}

/** Transcription: whisper model + device (blank = .env). */
export function TranscriptionSection({ s, save }: { s: AppSettings; save: SaveFn }) {
  const { t } = useTranslation();
  return (
    <Card title={t("pages.settings.transcribe_title")} hint={t("pages.settings.transcribe_hint")} testId="settings-transcribe">
      <Field label={t("pages.settings.whisper_model")} hint={t("pages.settings.whisper_hint")}>
        <select className={selectCls} data-testid="whisper-model" value={s.whisper_model ?? ""}
          onChange={(e) => void save({ whisper_model: e.target.value || null })}>
          <option value="">{t("pages.settings.from_env", { value: s.effective.env_whisper_model })}</option>
          {s.choices.whisper_models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("pages.settings.device")} hint={t("pages.settings.device_hint")}>
        <select className={selectCls} data-testid="device" value={s.device ?? ""} onChange={(e) => void save({ device: (e.target.value || null) as Device | null })}>
          <option value="">{t("pages.settings.from_env", { value: t(`device_choice.${s.effective.env_device}`) })}</option>
          {s.choices.devices.map((d) => (
            <option key={d} value={d}>
              {t(`device_choice.${d}`)}
            </option>
          ))}
        </select>
      </Field>
    </Card>
  );
}

/** Rendering defaults + output folder. */
export function RenderSection({ s, save }: { s: AppSettings; save: SaveFn }) {
  const { t } = useTranslation();
  return (
    <Card title={t("pages.settings.render_title")} hint={t("pages.settings.render_hint")} testId="settings-render">
      <Field label={t("pages.editor.renderer")} hint={t(`pages.editor.${s.renderer}_hint`)}>
        <select className={selectCls} data-testid="settings-renderer" value={s.renderer} onChange={(e) => void save({ renderer: e.target.value as Renderer })}>
          {RENDERERS.map((r) => (
            <option key={r} value={r}>
              {t(`renderer.${r}`)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("pages.settings.default_platform")}>
        <select className={selectCls} data-testid="default-platform" value={s.default_platform} onChange={(e) => void save({ default_platform: e.target.value as Platform })}>
          {PLATFORMS.map((p) => (
            <option key={p} value={p}>
              {t(`platform.${p}`)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("pages.settings.default_caption_style")}>
        <select className={selectCls} data-testid="default-style" value={s.default_caption_style}
          onChange={(e) => void save({ default_caption_style: e.target.value as CaptionStyle })}>
          {CAPTION_STYLES.map((c) => (
            <option key={c} value={c}>
              {t(`caption_style.${c}`)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("pages.settings.output_dir")} hint={t("pages.settings.output_dir_hint")}>
        <TextInput label={t("pages.settings.output_dir")} testId="output-dir" value={s.output_dir} placeholder="data/output"
          onCommit={(v) => void save({ output_dir: v })} />
      </Field>
    </Card>
  );
}

const PRESET_FIELDS: { key: keyof PresetOverride; min: number; max: number }[] = [
  { key: "max_seconds", min: 5, max: 600 },
  { key: "video_bitrate_mbps", min: 1, max: 50 },
  { key: "audio_bitrate_kbps", min: 64, max: 320 },
];

/** Platform presets: max length, bitrates, fps per platform (overrides of the built-in defaults). */
export function PresetsSection({ s, save }: { s: AppSettings; save: SaveFn }) {
  const { t } = useTranslation();
  const [presets, setPresets] = useState<RenderPresets | null>(null);
  useEffect(() => {
    getRenderPresets().then(setPresets).catch(() => setPresets(null));
  }, [s.platform_presets]);
  if (!presets) return null;
  const setField = (p: Platform, key: keyof PresetOverride, v: number | null) =>
    void save({ platform_presets: { ...s.platform_presets, [p]: { ...(s.platform_presets[p] ?? {}), [key]: v } } });
  const reset = (p: Platform) => {
    const next = { ...s.platform_presets };
    delete next[p];
    void save({ platform_presets: next });
  };
  return (
    <Card title={t("pages.settings.presets_title")} hint={t("pages.settings.presets_hint")} testId="settings-presets">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="pb-2 font-normal">{t("pages.settings.platform")}</th>
            {PRESET_FIELDS.map((f) => (
              <th key={f.key} className="pb-2 font-normal">
                {t(`pages.settings.preset_${f.key}`)}
              </th>
            ))}
            <th className="pb-2 font-normal">{t("pages.settings.preset_fps")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {PLATFORMS.map((p) => {
            const cur = presets.presets[p];
            const changed = Boolean(s.platform_presets[p] && Object.keys(s.platform_presets[p]!).length);
            return (
              <tr key={p} className="border-t border-line" data-testid={`preset-${p}`}>
                <td className="py-2 pr-3">
                  {t(`platform.${p}`)}
                  {changed && <span className="ml-2 text-xs text-accent">{t("pages.settings.changed")}</span>}
                </td>
                {PRESET_FIELDS.map((f) => (
                  <td key={f.key} className="py-2 pr-3">
                    <NumberInput label={`${t(`platform.${p}`)} ${t(`pages.settings.preset_${f.key}`)}`} testId={`preset-${p}-${f.key}`}
                      value={cur[f.key] as number} min={f.min} max={f.max} onCommit={(v) => setField(p, f.key, v)} className="w-20" />
                  </td>
                ))}
                <td className="py-2 pr-3">
                  <select className={`${selectCls} w-28`} aria-label={`${t(`platform.${p}`)} ${t("pages.settings.preset_fps")}`} value={cur.fps ?? ""}
                    onChange={(e) => setField(p, "fps", e.target.value ? Number(e.target.value) : null)}>
                    <option value="">{t("pages.settings.fps_clip")}</option>
                    {[24, 25, 30, 50, 60].map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 text-right">
                  {changed && <ResetButton label={t("pages.settings.reset_defaults")} onClick={() => reset(p)} />}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
