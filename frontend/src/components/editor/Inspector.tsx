import { Loader2, Plus, RotateCw, Trash2, Wand2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { Clip, Edl } from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { CROP_MODES, EASINGS, sortKeyframes, type CropMode, type Keyframe } from "../../lib/edl";
import { formatPercent } from "../../lib/format";
import { TimeField } from "../TimeField";
import { Tooltip } from "../Tooltip";
import type { SplitPanel } from "./SourceView";

export const INSPECTOR_TABS = ["framing", "captions", "hook", "voice", "music", "audio", "export"] as const;
export type InspectorTab = (typeof INSPECTOR_TABS)[number];

/** Number input that commits on blur / Enter (Esc reverts); arrow keys step. */
export function NumberField({
  value,
  onCommit,
  step = 0.01,
  min,
  max,
  label,
  digits = 3,
  testId,
}: {
  value: number;
  onCommit: (v: number) => void;
  step?: number;
  min: number;
  max: number;
  label: string;
  digits?: number;
  testId?: string;
}) {
  const [text, setText] = useState(String(+value.toFixed(digits)));
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setText(String(+value.toFixed(digits)));
  }, [value, focused, digits]);
  const commit = (raw: string) => {
    const v = Number(raw.replace(",", "."));
    if (!Number.isFinite(v)) {
      setText(String(+value.toFixed(digits)));
      return;
    }
    const clamped = Math.min(max, Math.max(min, v));
    setText(String(+clamped.toFixed(digits)));
    if (Math.abs(clamped - value) > 1e-9) onCommit(clamped);
  };
  return (
    <input
      aria-label={label}
      data-testid={testId}
      inputMode="decimal"
      value={text}
      onFocus={() => setFocused(true)}
      onBlur={(e) => {
        setFocused(false);
        commit(e.target.value);
      }}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") commit((e.target as HTMLInputElement).value);
        if (e.key === "Escape") setText(String(+value.toFixed(digits)));
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const next = Math.min(max, Math.max(min, value + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1)));
          onCommit(+next.toFixed(digits));
        }
      }}
      className="w-full min-w-0 rounded-md border border-line bg-app px-1.5 py-1 text-right font-mono text-xs outline-none focus:border-accent"
    />
  );
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="border-b border-line px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="mb-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] items-center gap-3 text-sm">
      <span className="min-w-0 text-muted" title={hint}>
        {label}
      </span>
      <span className="min-w-0">{children}</span>
    </label>
  );
}

export const selectCls = "w-full min-w-0 rounded-md border border-line bg-app px-2 py-1 text-sm outline-none focus:border-accent";

export function FramingTab({
  clip,
  edl,
  clipTime,
  duration,
  selectedKeyframe,
  drawTarget,
  onCrop,
  onAddKeyframe,
  onSelectKeyframe,
  onSeek,
  onSetMode,
  onRecompute,
  onSplitRatio,
  onDraw,
}: {
  clip: Clip;
  edl: Edl;
  clipTime: number;
  duration: number;
  selectedKeyframe: number | null;
  drawTarget: SplitPanel | null;
  onCrop: (crop: Edl["crop"]) => void;
  onAddKeyframe: () => void;
  onSelectKeyframe: (i: number | null) => void;
  onSeek: (t: number) => void;
  onSetMode: (mode: CropMode) => void;
  onRecompute: () => void;
  onSplitRatio: (ratio: number, phase: "move" | "end") => void;
  onDraw: (panel: SplitPanel | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const crop = edl.crop;
  const keyframes = sortKeyframes(crop.keyframes);
  const reframe = clip.work.reframe;
  const prepare = clip.work.prepare;
  const running = clip.busy;
  const info = (reframe.info ?? {}) as { method?: string; found_ratio?: number; applied?: boolean; face_backend?: string };

  const setKeyframe = (i: number, change: Partial<Keyframe>) => {
    const next = keyframes.map((k, j) => (j === i ? { ...k, ...change } : k));
    onCrop({ ...crop, keyframes: sortKeyframes(next) });
  };

  return (
    <div data-testid="framing-tab">
      <Section title={t("pages.editor.mode")}>
        <div className="grid grid-cols-4 gap-1 rounded-lg border border-line bg-app p-1" role="radiogroup" aria-label={t("pages.editor.mode")}>
          {CROP_MODES.map((m) => (
            <Tooltip key={m} label={t(`pages.editor.mode_hint.${m}`)} side="bottom">
              <button
                type="button"
                role="radio"
                aria-checked={crop.mode === m}
                data-testid={`mode-${m}`}
                onClick={() => onSetMode(m)}
                className={`w-full truncate rounded-md px-1 py-1.5 text-xs ${crop.mode === m ? "bg-accent text-white" : "text-muted hover:bg-white/5 hover:text-fg"}`}
              >
                {t(`pages.editor.mode_name.${m}`)}
              </button>
            </Tooltip>
          ))}
        </div>

        <div className="mt-3 rounded-lg border border-line bg-app/50 p-2.5 text-xs" data-testid="reframe-status" data-state={running ? "running" : reframe.state}>
          {running ? (
            <span className="flex items-center gap-2 text-muted">
              <Loader2 className="size-3.5 animate-spin" />
              {prepare.state === "running" || prepare.state === "queued" ? t("pages.editor.preparing") : t("pages.editor.reframing")}
            </span>
          ) : reframe.state === "error" && reframe.error ? (
            <span className="flex items-start gap-2 text-danger">
              <span className="min-w-0 flex-1">{translateIssue(t, reframe.error)}</span>
            </span>
          ) : reframe.state === "done" ? (
            <span className="text-muted">
              {t(`pages.editor.method.${info.method ?? "center"}`, { defaultValue: info.method ?? "" })}
              {typeof info.found_ratio === "number" && info.method !== "already_vertical" && info.method !== "center" && (
                <> · {t("pages.editor.found_ratio", { value: formatPercent(info.found_ratio, i18n.language) })}</>
              )}
              {info.applied === false && <> · {t("pages.editor.kept_manual")}</>}
            </span>
          ) : (
            <span className="text-muted">{t("pages.editor.reframe_pending")}</span>
          )}
          {prepare.state === "error" && prepare.error && <div className="mt-1 text-danger">{translateIssue(t, prepare.error)}</div>}
        </div>
        <Tooltip label={t("pages.editor.recompute_hint")} side="bottom">
          <button
            type="button"
            data-testid="recompute"
            disabled={running}
            onClick={onRecompute}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-white/5 disabled:opacity-40"
          >
            {reframe.state === "error" ? <RotateCw className="size-4" /> : <Wand2 className="size-4" />}
            <span className="truncate">{t("pages.editor.recompute")}</span>
          </button>
        </Tooltip>
      </Section>

      {crop.mode === "split" && crop.split ? (
        <Section title={t("pages.editor.split")}>
          <Row label={t("pages.editor.split_ratio")}>
            <span className="flex items-center gap-2">
              <input
                type="range"
                min={0.2}
                max={0.8}
                step={0.01}
                value={crop.split.ratio}
                aria-label={t("pages.editor.split_ratio")}
                data-testid="split-ratio"
                onChange={(e) => onSplitRatio(Number(e.target.value), "move")}
                onPointerUp={(e) => onSplitRatio(Number((e.target as HTMLInputElement).value), "end")}
                onKeyUp={(e) => onSplitRatio(Number((e.target as HTMLInputElement).value), "end")}
                className="min-w-0 flex-1 accent-[var(--color-accent)]"
              />
              <span className="w-10 text-right font-mono text-xs">{Math.round(crop.split.ratio * 100)}%</span>
            </span>
          </Row>
          <div className="grid grid-cols-2 gap-2">
            {(["top", "bottom"] as SplitPanel[]).map((panel) => (
              <button
                key={panel}
                type="button"
                aria-pressed={drawTarget === panel}
                data-testid={`draw-${panel}`}
                onClick={() => onDraw(drawTarget === panel ? null : panel)}
                className={`truncate rounded-lg border px-2 py-1.5 text-xs ${drawTarget === panel ? "border-accent bg-accent/15 text-fg" : "border-line text-muted hover:bg-white/5"}`}
              >
                {t(`pages.editor.draw_${panel}`)}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">{t("pages.editor.split_hint")}</p>
        </Section>
      ) : (
        <>
          <Section title={t("pages.editor.motion")}>
            <Row label={t("pages.editor.easing")}>
              <select
                className={selectCls}
                value={crop.easing}
                data-testid="easing"
                onChange={(e) => onCrop({ ...crop, easing: e.target.value as Edl["crop"]["easing"] })}
              >
                {EASINGS.map((e) => (
                  <option key={e} value={e}>
                    {t(`pages.editor.easing_name.${e}`)}
                  </option>
                ))}
              </select>
            </Row>
            <Row label={t("pages.editor.fit")} hint={t("pages.editor.fit_hint")}>
              <select className={selectCls} value={crop.fit} data-testid="fit" onChange={(e) => onCrop({ ...crop, fit: e.target.value as Edl["crop"]["fit"] })}>
                <option value="fill">{t("pages.editor.fit_name.fill")}</option>
                <option value="blur">{t("pages.editor.fit_name.blur")}</option>
              </select>
            </Row>
          </Section>
          <Section
            title={t("pages.editor.keyframes", { count: keyframes.length })}
            actions={
              <Tooltip label={t("pages.editor.add_keyframe")} shortcut="F" side="top">
                <button
                  type="button"
                  onClick={onAddKeyframe}
                  data-testid="add-keyframe"
                  className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs hover:bg-white/5"
                >
                  <Plus className="size-3.5" />
                  <span className="max-w-32 truncate">{t("pages.editor.add_keyframe_short")}</span>
                </button>
              </Tooltip>
            }
          >
            <div className="mb-1 grid grid-cols-[6rem_1fr_1fr_1fr_1.75rem] gap-1.5 px-1 text-[10px] text-muted uppercase">
              <span>{t("pages.editor.kf_time")}</span>
              <span className="text-right">X</span>
              <span className="text-right">Y</span>
              <span className="text-right">{t("pages.editor.kf_zoom")}</span>
              <span />
            </div>
            <div className="max-h-64 space-y-1 overflow-y-auto" data-testid="keyframe-list">
              {keyframes.map((k, i) => (
                <div
                  key={`${i}-${k.t}`}
                  data-testid="keyframe-row"
                  onClick={() => {
                    onSelectKeyframe(i);
                    onSeek(k.t);
                  }}
                  className={`grid grid-cols-[6rem_1fr_1fr_1fr_1.75rem] items-center gap-1.5 rounded-md p-1 ${
                    selectedKeyframe === i ? "bg-accent/15 ring-1 ring-accent/60" : Math.abs(k.t - clipTime) < 0.02 ? "bg-white/5" : ""
                  }`}
                >
                  <TimeField value={k.t} min={0} max={duration} label={t("pages.editor.kf_time")} onChange={(v) => setKeyframe(i, { t: v })} className="w-full" />
                  <NumberField value={k.x} min={0} max={1} label="X" onCommit={(v) => setKeyframe(i, { x: v })} testId="kf-x" />
                  <NumberField value={k.y} min={0} max={1} label="Y" onCommit={(v) => setKeyframe(i, { y: v })} testId="kf-y" />
                  <NumberField value={k.zoom} min={1} max={4} step={0.05} digits={2} label={t("pages.editor.kf_zoom")} onCommit={(v) => setKeyframe(i, { zoom: v })} testId="kf-zoom" />
                  <Tooltip label={t("pages.editor.remove_keyframe")} side="top">
                    <button
                      type="button"
                      disabled={keyframes.length <= 1}
                      aria-label={t("pages.editor.remove_keyframe")}
                      data-testid="remove-keyframe"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectKeyframe(null);
                        onCrop({ ...crop, keyframes: keyframes.filter((_, j) => j !== i) });
                      }}
                      className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-white/5 hover:text-danger disabled:opacity-30"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </Tooltip>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">{t("pages.editor.keyframes_hint")}</p>
          </Section>
        </>
      )}
    </div>
  );
}

export function AudioTab({
  edl,
  onAudio,
  onVoiceover,
  isolate,
}: {
  edl: Edl;
  onAudio: (a: Edl["audio"]) => void;
  onVoiceover: (v: Edl["voiceover"]) => void;
  isolate: ReactNode;
}) {
  const { t } = useTranslation();
  const a = edl.audio;
  return (
    <div data-testid="audio-tab">
      <Section title={t("pages.editor.audio_original")}>
        <Row label={t("pages.editor.volume")}>
          <span className="flex items-center gap-2">
            <input
              type="range"
              min={-30}
              max={12}
              step={1}
              value={a.volume_db}
              aria-label={t("pages.editor.volume")}
              data-testid="volume"
              onChange={(e) => onAudio({ ...a, volume_db: Number(e.target.value) })}
              className="min-w-0 flex-1 accent-[var(--color-accent)]"
            />
            <span className="w-14 text-right font-mono text-xs">{t("pages.editor.db", { value: a.volume_db > 0 ? `+${a.volume_db}` : a.volume_db })}</span>
          </span>
        </Row>
        <Row label={t("pages.editor.target_lufs")} hint={t("pages.editor.target_lufs_hint")}>
          <NumberField value={a.normalize_lufs} min={-30} max={-6} step={0.5} digits={1} label={t("pages.editor.target_lufs")} onCommit={(v) => onAudio({ ...a, normalize_lufs: v })} testId="lufs" />
        </Row>
        {/* not a <label>: the control opens a confirmation dialog */}
        <div className="mb-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] items-center gap-3 text-sm">
          <span className="min-w-0 text-muted" title={t("pages.editor.isolate_hint")}>
            {t("pages.editor.isolate")}
          </span>
          <span className="min-w-0">{isolate}</span>
        </div>
        {a.isolate_voice && <p className="text-xs text-muted">{t("pages.editor.isolate_on_note")}</p>}
      </Section>
      <Section title={t("pages.editor.ducking")}>
        <Row label={t("pages.editor.duck_db")} hint={t("pages.editor.duck_hint")}>
          <span className="flex items-center gap-2">
            <input
              type="range"
              min={-40}
              max={0}
              step={1}
              value={edl.voiceover.duck_db}
              aria-label={t("pages.editor.duck_db")}
              onChange={(e) => onVoiceover({ ...edl.voiceover, duck_db: Number(e.target.value) })}
              className="min-w-0 flex-1 accent-[var(--color-accent)]"
            />
            <span className="w-14 text-right font-mono text-xs">{t("pages.editor.db", { value: edl.voiceover.duck_db })}</span>
          </span>
        </Row>
        <p className="text-xs text-muted">{edl.voiceover.audio_path ? t("pages.editor.duck_active") : t("pages.editor.duck_note")}</p>
      </Section>
    </div>
  );
}
