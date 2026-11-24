import { useTranslation } from "react-i18next";

import { STEPS, type Source, type StepState } from "../../lib/api";
import { formatPercent } from "../../lib/format";
import { Tooltip } from "../Tooltip";

// Bar color + label color per state (no icons: labels need the full column width, esp. in Vietnamese).
const STYLE: Record<StepState, { bar: string; text: string }> = {
  pending: { bar: "bg-white/10", text: "text-muted" },
  queued: { bar: "bg-white/20", text: "text-muted" },
  running: { bar: "bg-accent/30", text: "text-accent" },
  done: { bar: "bg-success", text: "text-fg" },
  skipped: { bar: "bg-white/25", text: "text-muted line-through" },
  error: { bar: "bg-danger", text: "text-danger" },
  waiting: { bar: "bg-warning", text: "text-warning" },
};

/** Mini stepper: Preflight → Preview copy → Transcribe → Analyze → Moments. */
export function StepStepper({ source }: { source: Source }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? "en";
  return (
    <ol className="grid grid-cols-5 gap-1.5" data-testid="step-stepper">
      {STEPS.map((step) => {
        const info = source.steps[step] ?? { state: "pending", progress: 0 };
        const style = STYLE[info.state] ?? STYLE.pending;
        const running = info.state === "running";
        const stateLabel = running ? formatPercent(info.progress, locale) : t(`step_state.${info.state}`);
        return (
          <li key={step} className="min-w-0" data-step={step} data-state={info.state}>
            <Tooltip label={t("pages.library.step_tooltip", { step: t(`steps.${step}`), state: stateLabel })} side="top">
              <div className="w-full min-w-0">
                <div className={`relative h-1.5 overflow-hidden rounded-full ${style.bar}`}>
                  {running && (
                    <div
                      className="absolute inset-y-0 left-0 rounded-full bg-accent transition-[width] duration-500"
                      style={{ width: `${Math.max(4, info.progress * 100)}%` }}
                    />
                  )}
                </div>
                <div className={`mt-1.5 text-xs leading-tight [overflow-wrap:anywhere] ${style.text}`}>{t(`steps.${step}`)}</div>
                <div className={`mt-0.5 text-[11px] ${running ? "font-mono text-accent" : "text-muted"}`}>{stateLabel}</div>
              </div>
            </Tooltip>
          </li>
        );
      })}
    </ol>
  );
}
