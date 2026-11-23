import { AudioLines, Crosshair, Loader2, Pause, Play, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  VOICE_MODELS,
  estimateIsolation,
  estimateVoiceover,
  generateVoiceover,
  getVoiceStatus,
  isolateVoice,
  listVoices,
  suggestVoiceover,
  voicePreviewUrl,
  type ApiIssue,
  type Clip,
  type Edl,
  type IsolationEstimate,
  type Voice,
  type VoiceEstimate,
  type VoiceRequest,
  type VoiceSettings,
  type VoiceStatus,
} from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { formatTimecode } from "../../lib/format";
import { ConfirmDialog } from "../ConfirmDialog";
import { TimeField } from "../TimeField";
import { useToast } from "../Toasts";
import { Tooltip } from "../Tooltip";
import { Row, Section, selectCls } from "./Inspector";

type Mode = "record" | "live";
const inputCls = "w-full min-w-0 rounded-md border border-line bg-app px-2 py-1 text-sm outline-none focus:border-accent";
const rangeCls = "min-w-0 flex-1 accent-[var(--color-accent)]";
const SLIDERS: { key: keyof VoiceSettings; min: number; max: number; step: number }[] = [
  { key: "stability", min: 0, max: 1, step: 0.05 },
  { key: "similarity_boost", min: 0, max: 1, step: 0.05 },
  { key: "style", min: 0, max: 1, step: 0.05 },
  { key: "speed", min: 0.7, max: 1.2, step: 0.01 },
];

const issueOf = (err: unknown): ApiIssue => (err instanceof ApiError ? err.issue : { code: "UNKNOWN", severity: "error", params: {} });
const needsConfirm = (err: unknown) => err instanceof ApiError && err.issue.code === "VOICE_CONFIRM_REQUIRED";

/** Shared by the Voice and Audio tabs: ElevenLabs status (configured / mock / usage), loaded once per mount. */
export function useVoiceStatus() {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const refresh = useCallback((force = false) => {
    getVoiceStatus(force).then(setStatus, () => setStatus(null));
  }, []);
  useEffect(() => refresh(), [refresh]);
  return { status, refresh };
}

function n(value: number | null | undefined, locale: string) {
  return value === null || value === undefined ? "–" : new Intl.NumberFormat(locale).format(value);
}

/** One line under the tab title: not configured / mock mode / credits used this period. */
export function VoiceStatusNote({ status }: { status: VoiceStatus | null }) {
  const { t, i18n } = useTranslation();
  if (!status) return null;
  if (!status.configured) {
    return (
      <div className="rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-xs text-warning" data-testid="voice-not-configured">
        {t("pages.editor.voice_not_configured")}
      </div>
    );
  }
  const sub = status.subscription;
  return (
    <div className="space-y-1 rounded-lg border border-line bg-app/50 p-2.5 text-xs text-muted" data-testid="voice-status" data-mock={status.mock ? "1" : "0"}>
      {status.mock && <p className="text-warning">{t("pages.editor.voice_mock")}</p>}
      {sub && sub.character_limit !== null ? (
        <p>{t("pages.editor.voice_usage", { used: n(sub.character_count, i18n.language), limit: n(sub.character_limit, i18n.language) })}</p>
      ) : status.error ? (
        <p className="text-danger">{t(`errors.${status.error}`, { defaultValue: status.error })}</p>
      ) : null}
    </div>
  );
}

/** Asks before spending credits; the server answers VOICE_CONFIRM_REQUIRED unless the result is cached. */
function CreditDialog({
  ask,
  title,
  body,
  remaining,
  busy,
  onConfirm,
  onCancel,
}: {
  ask: { credits: number } | null;
  title: string;
  body: string;
  remaining: number | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <ConfirmDialog
      open={!!ask}
      title={title}
      confirmLabel={t("pages.editor.voice_spend", { credits: n(ask?.credits, i18n.language) })}
      busy={busy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p data-testid="credit-body">{body}</p>
      {remaining !== null && ask && (
        <p className={remaining - ask.credits < 0 ? "text-danger" : ""}>
          {t("pages.editor.voice_remaining", { left: n(Math.max(0, remaining - ask.credits), i18n.language) })}
        </p>
      )}
    </ConfirmDialog>
  );
}

const remainingOf = (status: VoiceStatus | null) => {
  const sub = status?.subscription;
  return sub && sub.character_limit !== null && sub.character_count !== null ? sub.character_limit - sub.character_count : null;
};

export function VoiceTab({
  clip,
  edl,
  clipTime,
  duration,
  language,
  onVoiceover,
  onSeek,
  onPlayFrom,
  onBeforeCall,
  onServerClip,
}: {
  clip: Clip;
  edl: Edl;
  clipTime: number;
  duration: number;
  language: string;
  onVoiceover: (v: Edl["voiceover"], mode?: Mode, coalesceKey?: string) => void;
  onSeek: (t: number) => void;
  onPlayFrom: (t: number) => void;
  /** Save pending edits before the server changes the clip. */
  onBeforeCall: () => Promise<void>;
  /** Adopt the server's clip (one undo step). */
  onServerClip: (clip: Clip, sections: ("voiceover" | "audio")[]) => void;
}) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const { status, refresh } = useVoiceStatus();
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [voicesError, setVoicesError] = useState<ApiIssue | null>(null);
  const [est, setEst] = useState<VoiceEstimate | null>(null);
  const [busy, setBusy] = useState<"suggest" | "generate" | null>(null);
  const [ask, setAsk] = useState<{ characters: number; credits: number } | null>(null);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const sample = useRef<HTMLAudioElement | null>(null);
  const vo = edl.voiceover;
  const configured = !!status?.configured;

  // voices (free call, cached on the backend)
  useEffect(() => {
    if (!configured) return;
    let cancelled = false;
    listVoices().then(
      (list) => !cancelled && (setVoices(list), setVoicesError(null)),
      (err) => !cancelled && setVoicesError(issueOf(err)),
    );
    return () => {
      cancelled = true;
    };
  }, [configured]);
  useEffect(() => () => sample.current?.pause(), []);

  const voiceId = vo.voice_id || status?.default_voice_id || voices?.[0]?.voice_id || "";
  const request: VoiceRequest = { script: vo.script, voice_id: voiceId, model_id: vo.model_id, settings: vo.settings };
  const requestKey = JSON.stringify(request);

  // character count + credits (never calls ElevenLabs), debounced
  useEffect(() => {
    const handle = window.setTimeout(() => {
      estimateVoiceover(clip.id, JSON.parse(requestKey) as VoiceRequest).then(setEst, () => setEst(null));
    }, 300);
    return () => window.clearTimeout(handle);
  }, [clip.id, requestKey, vo.audio_path]);

  const model = status?.models.find((m) => m.id === vo.model_id);
  const chars = est?.characters ?? vo.script.trim().length;
  const overLimit = !!est?.over_limit;
  const stale = !!vo.audio_path && !!est && !!vo.script.trim() && est.audio_path !== vo.audio_path;
  const canGenerate = !!vo.script.trim() && !!voiceId && !overLimit && busy === null && (configured || !!est?.cached);

  const run = async (confirm: boolean) => {
    setBusy("generate");
    try {
      if (!confirm) await onBeforeCall();
      const res = await generateVoiceover(clip.id, { ...request, confirm });
      setAsk(null);
      onServerClip(res.clip, ["voiceover"]);
      toast.show(res.cached ? t("pages.editor.voice_reused") : t("pages.editor.voice_ready", { credits: n(res.credits, i18n.language) }), "success");
      if (!res.cached) refresh(true);
    } catch (err) {
      if (needsConfirm(err)) {
        const p = (err as ApiError).issue.params as { characters: number; credits: number };
        setAsk({ characters: p.characters, credits: p.credits });
      } else {
        setAsk(null);
        toast.show(translateIssue(t, issueOf(err)), "error");
      }
    } finally {
      setBusy(null);
    }
  };

  const suggest = async () => {
    setBusy("suggest");
    try {
      await onBeforeCall();
      const res = await suggestVoiceover(clip.id);
      onVoiceover({ ...vo, script: res.script });
      toast.show(t(res.source === "heuristic" ? "pages.editor.voice_suggested_heuristic" : "pages.editor.voice_suggested", { provider: res.source }), "info");
    } catch (err) {
      toast.show(translateIssue(t, issueOf(err)), "error");
    } finally {
      setBusy(null);
    }
  };

  const playSample = (v: Voice | undefined) => {
    sample.current?.pause();
    if (!v || previewing === v.voice_id) return setPreviewing(null);
    const url = voicePreviewUrl(v.preview_url);
    if (!url) return;
    const audio = new Audio(url);
    sample.current = audio;
    audio.onended = () => setPreviewing(null);
    setPreviewing(v.voice_id);
    void audio.play().catch(() => setPreviewing(null));
  };

  const selected = voices?.find((v) => v.voice_id === voiceId);
  const langWarning = language === "vi" && model && !model.vietnamese;

  return (
    <div data-testid="voice-tab">
      <div className="px-4 pt-3">
        <VoiceStatusNote status={status} />
      </div>

      <Section
        title={t("pages.editor.voice_script")}
        actions={
          <Tooltip label={t("pages.editor.voice_suggest_hint")} side="top">
            <button
              type="button"
              data-testid="voice-suggest"
              disabled={busy !== null}
              onClick={() => void suggest()}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs hover:bg-white/5 disabled:opacity-40"
            >
              {busy === "suggest" ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
              <span className="max-w-36 truncate">{t("pages.editor.voice_suggest")}</span>
            </button>
          </Tooltip>
        }
      >
        <textarea
          value={vo.script}
          rows={4}
          data-testid="voice-script"
          aria-label={t("pages.editor.voice_script")}
          placeholder={t("pages.editor.voice_script_placeholder")}
          onChange={(e) => onVoiceover({ ...vo, script: e.target.value }, "record", "vo-script")}
          onKeyDown={(e) => e.stopPropagation()}
          className={`${inputCls} resize-y`}
          data-content
        />
        <div className="mt-1 flex items-center justify-between gap-2 font-mono text-[11px]" data-testid="voice-estimate" data-credits={est?.credits_to_spend ?? ""} data-cached={est?.cached ? "1" : "0"}>
          <span className={overLimit ? "text-danger" : "text-muted"}>
            {t("pages.editor.voice_chars", { chars: n(chars, i18n.language), max: n(est?.max_chars ?? model?.max_chars, i18n.language) })}
          </span>
          {est &&
            (est.cached ? (
              <span className="text-success">{t("pages.editor.voice_cached")}</span>
            ) : (
              <span className="text-muted">{t("pages.editor.voice_credits", { credits: n(est.credits, i18n.language) })}</span>
            ))}
        </div>
        {overLimit && <p className="mt-1 text-xs text-danger">{t("errors.VOICE_SCRIPT_TOO_LONG", { max: est?.max_chars })}</p>}
      </Section>

      <Section title={t("pages.editor.voice_voice")}>
        <Row label={t("pages.editor.voice_voice")}>
          <span className="flex items-center gap-1.5">
            <select
              className={selectCls}
              value={voiceId}
              data-testid="voice-select"
              disabled={!voices?.length}
              onChange={(e) => onVoiceover({ ...vo, voice_id: e.target.value })}
            >
              {!voices?.length && <option value={voiceId}>{voiceId || t("pages.editor.voice_none")}</option>}
              {voices?.map((v) => (
                <option key={v.voice_id} value={v.voice_id}>
                  {v.name}
                  {v.labels.accent ? ` · ${v.labels.accent}` : ""}
                </option>
              ))}
            </select>
            <Tooltip label={t("pages.editor.voice_sample")} side="top">
              <button
                type="button"
                aria-label={t("pages.editor.voice_sample")}
                data-testid="voice-sample"
                disabled={!selected?.preview_url}
                onClick={() => playSample(selected)}
                className="flex size-7 shrink-0 items-center justify-center rounded-md border border-line text-muted hover:bg-white/5 hover:text-fg disabled:opacity-30"
              >
                {previewing === selected?.voice_id ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
              </button>
            </Tooltip>
          </span>
        </Row>
        {voicesError && <p className="mb-2 text-xs text-danger">{translateIssue(t, voicesError)}</p>}
        <Row label={t("pages.editor.voice_model")} hint={t("pages.editor.voice_model_hint")}>
          <select className={selectCls} value={vo.model_id} data-testid="voice-model" onChange={(e) => onVoiceover({ ...vo, model_id: e.target.value as Edl["voiceover"]["model_id"] })}>
            {VOICE_MODELS.map((m) => (
              <option key={m} value={m}>
                {t(`pages.editor.voice_model_name.${m}`)}
              </option>
            ))}
          </select>
        </Row>
        {model && (
          <p className="-mt-1 mb-2 text-right text-[11px] text-muted">
            {t("pages.editor.voice_model_rate", { rate: n(model.credits_per_char, i18n.language), max: n(model.max_chars, i18n.language) })}
          </p>
        )}
        {langWarning && <p className="mb-2 text-xs text-warning">{t("pages.editor.voice_model_no_vi")}</p>}
        {SLIDERS.map(({ key, min, max, step }) => (
          <Row key={key} label={t(`pages.editor.voice_setting.${key}`)} hint={t(`pages.editor.voice_setting_hint.${key}`)}>
            <span className="flex items-center gap-2">
              <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={vo.settings[key]}
                aria-label={t(`pages.editor.voice_setting.${key}`)}
                data-testid={`voice-${key}`}
                onChange={(e) => onVoiceover({ ...vo, settings: { ...vo.settings, [key]: Number(e.target.value) } }, "record", `vo-${key}`)}
                className={rangeCls}
              />
              <span className="w-10 text-right font-mono text-xs">{key === "speed" ? `${vo.settings[key].toFixed(2)}×` : vo.settings[key].toFixed(2)}</span>
            </span>
          </Row>
        ))}
        {vo.model_id === "eleven_v3" && <p className="mb-2 text-xs text-muted">{t("pages.editor.voice_v3_stability")}</p>}

        <button
          type="button"
          data-testid="voice-generate"
          disabled={!canGenerate}
          onClick={() => void run(false)}
          className="mt-1 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-40"
        >
          {busy === "generate" ? <Loader2 className="size-4 animate-spin" /> : <AudioLines className="size-4" />}
          <span className="truncate">
            {est?.cached ? t("pages.editor.voice_use_saved") : vo.audio_path ? t("pages.editor.voice_regenerate") : t("pages.editor.voice_generate")}
          </span>
        </button>
        {stale && <p className="mt-2 text-xs text-warning" data-testid="voice-stale">{t("pages.editor.voice_stale")}</p>}
        <p className="mt-2 text-xs text-muted">{t("pages.editor.voice_cache_note")}</p>
      </Section>

      <Section
        title={t("pages.editor.voice_placement")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={vo.enabled}
              disabled={!vo.audio_path}
              data-testid="voice-enabled"
              onChange={(e) => onVoiceover({ ...vo, enabled: e.target.checked })}
              className="accent-[var(--color-accent)]"
            />
            {t("pages.editor.voice_use")}
          </label>
        }
      >
        {vo.audio_path ? (
          <>
            <Row label={t("pages.editor.voice_start")}>
              <span className="flex items-center gap-1.5">
                <TimeField value={vo.start} max={Math.max(0, duration - 0.1)} label={t("pages.editor.voice_start")} onChange={(v) => onVoiceover({ ...vo, start: v })} testId="voice-start" className="min-w-0 flex-1" />
                <Tooltip label={t("pages.editor.voice_at_playhead")} side="top">
                  <button
                    type="button"
                    aria-label={t("pages.editor.voice_at_playhead")}
                    data-testid="voice-at-playhead"
                    onClick={() => onVoiceover({ ...vo, start: Math.round(clipTime * 100) / 100 })}
                    className="flex size-7 shrink-0 items-center justify-center rounded-md border border-line text-muted hover:bg-white/5 hover:text-fg"
                  >
                    <Crosshair className="size-3.5" />
                  </button>
                </Tooltip>
              </span>
            </Row>
            <Row label={t("pages.editor.voice_length")}>
              <span className="font-mono text-xs" data-testid="voice-duration">
                {formatTimecode(vo.duration ?? 0)}
                {vo.duration !== null && vo.start + vo.duration > duration + 0.05 && <span className="ml-2 font-sans text-warning">{t("pages.editor.voice_too_long")}</span>}
              </span>
            </Row>
            <Row label={t("pages.editor.voice_volume")}>
              <span className="flex items-center gap-2">
                <input
                  type="range"
                  min={-20}
                  max={6}
                  step={1}
                  value={vo.volume_db}
                  aria-label={t("pages.editor.voice_volume")}
                  data-testid="voice-volume"
                  onChange={(e) => onVoiceover({ ...vo, volume_db: Number(e.target.value) }, "record", "vo-volume")}
                  className={rangeCls}
                />
                <span className="w-14 text-right font-mono text-xs">{t("pages.editor.db", { value: vo.volume_db > 0 ? `+${vo.volume_db}` : vo.volume_db })}</span>
              </span>
            </Row>
            <Row label={t("pages.editor.duck_db")} hint={t("pages.editor.duck_hint")}>
              <span className="flex items-center gap-2">
                <input
                  type="range"
                  min={-40}
                  max={0}
                  step={1}
                  value={vo.duck_db}
                  aria-label={t("pages.editor.duck_db")}
                  data-testid="voice-duck"
                  onChange={(e) => onVoiceover({ ...vo, duck_db: Number(e.target.value) }, "record", "duck")}
                  className={rangeCls}
                />
                <span className="w-14 text-right font-mono text-xs">{t("pages.editor.db", { value: vo.duck_db })}</span>
              </span>
            </Row>
            <div className="flex gap-2">
              <button
                type="button"
                data-testid="voice-play"
                onClick={() => onPlayFrom(vo.start)}
                className="flex min-w-0 flex-1 items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-white/5"
              >
                <Play className="size-3.5" />
                <span className="truncate">{t("pages.editor.voice_play")}</span>
              </button>
              <button
                type="button"
                onClick={() => onSeek(vo.start)}
                className="min-w-0 truncate rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-white/5"
              >
                {t("pages.editor.voice_go_start")}
              </button>
            </div>
            <p className="mt-2 text-xs text-muted">{t("pages.editor.voice_drag_hint")}</p>
          </>
        ) : (
          <p className="text-xs text-muted">{t("pages.editor.voice_none_yet")}</p>
        )}
      </Section>

      <CreditDialog
        ask={ask}
        title={t("pages.editor.voice_confirm_title")}
        body={t("pages.editor.voice_confirm_body", { characters: n(ask?.characters, i18n.language), credits: n(ask?.credits, i18n.language) })}
        remaining={remainingOf(status)}
        busy={busy === "generate"}
        onConfirm={() => void run(true)}
        onCancel={() => setAsk(null)}
      />
    </div>
  );
}

/** Voice Isolator toggle for the Audio tab: turning it on asks first unless the result is cached. */
export function IsolateToggle({
  clip,
  edl,
  onAudio,
  onBeforeCall,
  onServerClip,
}: {
  clip: Clip;
  edl: Edl;
  onAudio: (a: Edl["audio"]) => void;
  onBeforeCall: () => Promise<void>;
  onServerClip: (clip: Clip, sections: ("voiceover" | "audio")[]) => void;
}) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const { status, refresh } = useVoiceStatus();
  const [est, setEst] = useState<IsolationEstimate | null>(null);
  const [ask, setAsk] = useState<{ credits: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const a = edl.audio;
  const rangeKey = JSON.stringify(edl.segments);

  useEffect(() => {
    let cancelled = false;
    estimateIsolation(clip.id).then((e) => !cancelled && setEst(e), () => !cancelled && setEst(null));
    return () => {
      cancelled = true;
    };
  }, [clip.id, rangeKey, a.isolated_path]);

  const run = async (confirm: boolean) => {
    setBusy(true);
    try {
      if (!confirm) await onBeforeCall();
      const res = await isolateVoice(clip.id, confirm);
      setAsk(null);
      onServerClip(res.clip, ["audio"]);
      toast.show(res.cached ? t("pages.editor.isolate_reused") : t("pages.editor.isolate_ready", { credits: n(res.credits, i18n.language) }), "success");
      if (!res.cached) refresh(true);
    } catch (err) {
      if (needsConfirm(err)) setAsk({ credits: Number((err as ApiError).issue.params.credits) });
      else {
        setAsk(null);
        toast.show(translateIssue(t, issueOf(err)), "error");
      }
    } finally {
      setBusy(false);
    }
  };

  const usable = !!status?.configured || !!est?.cached;
  return (
    <>
      <span className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={a.isolate_voice}
          disabled={busy || (!a.isolate_voice && !usable)}
          aria-label={t("pages.editor.isolate")}
          data-testid="isolate-toggle"
          onChange={(e) => (e.target.checked ? void run(false) : onAudio({ ...a, isolate_voice: false }))}
          className="accent-[var(--color-accent)]"
        />
        {busy ? (
          <Loader2 className="size-3.5 animate-spin text-muted" />
        ) : (
          <span className="truncate text-xs text-muted" data-testid="isolate-estimate">
            {!usable
              ? t("pages.editor.needs_elevenlabs")
              : est?.cached
                ? t("pages.editor.voice_cached")
                : est
                  ? t("pages.editor.voice_credits", { credits: n(est.credits, i18n.language) })
                  : ""}
          </span>
        )}
      </span>
      <CreditDialog
        ask={ask}
        title={t("pages.editor.isolate_confirm_title")}
        body={t("pages.editor.isolate_confirm_body", { duration: formatTimecode(est?.seconds ?? 0), credits: n(ask?.credits, i18n.language) })}
        remaining={remainingOf(status)}
        busy={busy}
        onConfirm={() => void run(true)}
        onCancel={() => setAsk(null)}
      />
    </>
  );
}
