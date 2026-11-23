import { RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, clearCache, getCache, type CacheKey, type CacheSummary } from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { formatBytes } from "../../lib/format";
import { ConfirmDialog } from "../ConfirmDialog";
import { useToast } from "../Toasts";
import { Tooltip } from "../Tooltip";
import { Card } from "./Controls";

/** Settings → Storage: size of each cache, clear the rebuildable ones (paid voice files are never cleared). */
export function CachePanel() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const [data, setData] = useState<CacheSummary | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirm, setConfirm] = useState<CacheKey[] | "all" | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setFailed(false);
    getCache().then(setData, () => setFailed(true));
  }, []);
  useEffect(load, [load]);

  const run = async () => {
    const keys = confirm === "all" ? undefined : (confirm ?? undefined);
    setBusy(true);
    try {
      const out = await clearCache(keys);
      setData(out);
      toast.show(t("pages.settings.cache_freed", { size: formatBytes(out.freed_bytes ?? 0, i18n.language) }), "success");
      if (out.warning) toast.show(translateIssue(t, out.warning), "info");
    } catch (err) {
      toast.show(translateIssue(t, err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} }), "error");
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };

  const fmt = (n: number) => formatBytes(n, i18n.language);
  return (
    <Card
      title={t("pages.settings.cache_title")}
      hint={t("pages.settings.cache_hint")}
      testId="settings-cache"
      actions={
        <>
          <Tooltip label={t("pages.settings.cache_refresh")} side="top">
            <button type="button" aria-label={t("pages.settings.cache_refresh")} onClick={load}
              className="flex size-8 items-center justify-center rounded-md border border-line text-muted hover:bg-white/5 hover:text-fg">
              <RefreshCw className="size-4" />
            </button>
          </Tooltip>
          <Tooltip label={t("pages.settings.cache_clear_all_hint")} side="top">
            <button type="button" data-testid="cache-clear-all" disabled={!data || data.clearable_bytes === 0} onClick={() => setConfirm("all")}
              className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-sm hover:border-danger hover:text-danger disabled:opacity-40">
              <Trash2 className="size-4" />
              {t("pages.settings.cache_clear_all", { size: data ? fmt(data.clearable_bytes) : "…" })}
            </button>
          </Tooltip>
        </>
      }
    >
      {failed && (
        <p className="text-sm text-danger">
          {t("pages.settings.cache_failed")}{" "}
          <button type="button" className="underline" onClick={load}>
            {t("common.retry")}
          </button>
        </p>
      )}
      {!data && !failed && <div className="h-24 animate-pulse rounded-lg bg-white/5" />}
      {data && (
        <table className="w-full text-sm">
          <tbody>
            {data.items.map((it) => (
              <tr key={it.key} className="border-t border-line first:border-t-0" data-testid={`cache-${it.key}`}>
                <td className="py-2 pr-3">
                  {t(`cache.${it.key}`)}
                  <p className="text-xs text-muted">{t(`cache_hint.${it.key}`)}</p>
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-muted">{fmt(it.bytes)}</td>
                <td className="w-28 py-2 text-right">
                  {it.clearable ? (
                    <button type="button" disabled={it.bytes === 0} onClick={() => setConfirm([it.key])}
                      className="rounded-md border border-line px-2 py-1 text-xs hover:border-danger hover:text-danger disabled:opacity-40">
                      {t("pages.settings.cache_clear")}
                    </button>
                  ) : (
                    <span className="text-xs text-muted">{t("pages.settings.cache_kept")}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ConfirmDialog
        open={confirm !== null}
        danger
        busy={busy}
        title={t("pages.settings.cache_confirm_title")}
        confirmLabel={t("pages.settings.cache_clear")}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void run()}
      >
        {confirm === "all" || confirm === null
          ? t("pages.settings.cache_confirm_all")
          : t("pages.settings.cache_confirm_one", { name: t(`cache.${confirm[0]}`) })}
      </ConfirmDialog>
    </Card>
  );
}
