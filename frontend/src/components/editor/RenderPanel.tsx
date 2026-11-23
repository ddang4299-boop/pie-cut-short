import { AlertTriangle, CheckCircle2, Clapperboard, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import {
  ApiError,
  RENDERERS,
  getExport,
  getRenderPresets,
  getSettings,
  renderClip,
  type ExportClip,
  type Platform,
  type RenderPresets,
  type Renderer,
} from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { formatPercent } from "../../lib/format";
import { JobLog } from "../JobLog";
import { useToast } from "../Toasts";
import { Tooltip } from "../Tooltip";
import { Row, Section, selectCls } from "./Inspector";

const POLL_MS = 1500;

/** Editor → Export tab: platform length check, renderer choice, Render button + live status. */
export function RenderPanel({
  clipId,
  duration,
  platform,
  onBeforeCall,
}: {
  clipId: string;
  duration: number;
  platform: Platform;
  onBeforeCall: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const [presets, setPresets] = useState<RenderPresets | null>(null);
  const [defaultRenderer, setDefaultRenderer] = useState<Renderer>("remotion");
  const [renderer, setRenderer] = useState<Renderer | "">("");
  const [state, setState] = useState<ExportClip | null>(null);
  const [sending, setSending] = useState(false);
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    getRenderPresets().then(setPresets).catch(() => {});
    getSettings()
      .then((s) => setDefaultRenderer(s.renderer ?? "remotion"))
      .catch(() => {});
  }, []);

  const refresh = useCallback(() => getExport(clipId).then(setState).catch(() => {}), [clipId]);
  useEffect(() => void refresh(), [refresh]);

  const render = state?.render;
  const active = render?.state === "queued" || render?.state === "running";
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [active, refresh]);

  const start = async () => {
    setSending(true);
    try {
      await onBeforeCall();
      setState(await renderClip(clipId, renderer || undefined));
      toast.show(t("pages.export.queued_toast"), "success");
    } catch (err) {
      if (err instanceof ApiError) toast.show(translateIssue(t, err.issue), "error");
    } finally {
      setSending(false);
    }
  };

  const max = presets?.presets[platform]?.max_seconds;
  const tooLong = max != null && duration > max + 0.05;
  const seconds = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(duration);
  const chosen = renderer || defaultRenderer;

  return (
    <Section title={t("pages.editor.render_title")}>
      {max != null && (
        <div data-testid="length-check" className={`mb-3 flex items-start gap-2 text-xs ${tooLong ? "text-warning" : "text-muted"}`}>
          {tooLong ? <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> : <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />}
          <span>{tooLong ? translateIssue(t, { code: "CLIP_TOO_LONG", params: { seconds, max } }) : t("pages.editor.length_ok", { seconds, max })}</span>
        </div>
      )}
      <Row label={t("pages.editor.renderer")}>
        <select className={selectCls} value={renderer} data-testid="renderer" onChange={(e) => setRenderer(e.target.value as Renderer | "")}>
          <option value="">{t("pages.editor.renderer_default", { name: t(`renderer.${defaultRenderer}`) })}</option>
          {RENDERERS.map((r) => (
            <option key={r} value={r}>
              {t(`renderer.${r}`)}
            </option>
          ))}
        </select>
      </Row>
      <p className="mb-3 text-xs text-muted">{t(`pages.editor.${chosen}_hint`)}</p>
      <Tooltip label={t("pages.editor.render_hint")} side="top">
        <button
          type="button"
          data-testid="render-clip"
          disabled={sending || active}
          onClick={() => void start()}
          className="flex w-full items-center justify-center gap-2 rounded-lg btn-primary px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Clapperboard className="size-4" />
          {t("pages.editor.render_button")}
        </button>
      </Tooltip>
      {render && render.state !== "idle" && (
        <div className="mt-3 text-xs" data-testid="render-status">
          {render.state === "queued" && <p className="text-muted">{t("pages.editor.render_queued")}</p>}
          {render.state === "running" && (
            <>
              <p className="mb-1 text-muted">{t("pages.editor.render_progress", { percent: formatPercent(render.progress, i18n.language) })}</p>
              <div className="h-1.5 overflow-hidden rounded bg-white/10">
                <div className="h-full bg-accent transition-[width]" style={{ width: `${Math.round(render.progress * 100)}%` }} />
              </div>
            </>
          )}
          {render.state === "done" && (
            <div className="flex flex-col items-start gap-2">
              <p className="text-success">{t("pages.editor.render_done")}</p>
              <Tooltip label={t("pages.editor.go_export")} side="top">
                <button type="button" onClick={() => navigate("/export")} className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1 hover:border-accent">
                  <ExternalLink className="size-3.5" />
                  {t("pages.editor.go_export")}
                </button>
              </Tooltip>
            </div>
          )}
          {render.state === "error" && (
            <>
              <p className="text-danger">{render.error ? translateIssue(t, render.error) : t("pages.editor.render_failed")}</p>
              {render.job_id && (
                <button type="button" className="mt-1 text-muted underline hover:text-fg" data-testid="render-log-toggle" onClick={() => setShowLog((v) => !v)}>
                  {showLog ? t("common.hide_log") : t("common.view_log")}
                </button>
              )}
              {showLog && render.job_id && (
                <div className="mt-2">
                  <JobLog jobId={render.job_id} live={false} />
                </div>
              )}
            </>
          )}
          {render.state === "cancelled" && <p className="text-muted">{t("render_state.cancelled")}</p>}
          {render.state === "done" &&
            (render.info?.warnings ?? []).map((w, i) => (
              <p key={i} className="mt-1 flex items-start gap-1.5 text-warning">
                <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                {translateIssue(t, w)}
              </p>
            ))}
        </div>
      )}
    </Section>
  );
}
