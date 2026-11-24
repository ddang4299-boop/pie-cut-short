import { Link2, Upload as UploadIcon, X } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, importSource } from "../../lib/api";
import { translateIssue } from "../../lib/errors";
import { formatBytes, formatPercent } from "../../lib/format";
import { useSources } from "../../state/sources";
import { useToast } from "../Toasts";
import { Tooltip } from "../Tooltip";

/** Drop zone + "Choose file" + URL / local path field. Preflight runs as soon as a file lands. */
export function ImportPanel({ compact }: { compact: boolean }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? "en";
  const toast = useToast();
  const { upload, uploads, refresh, replaceSource, setCurrentSourceId } = useSources();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [location, setLocation] = useState("");
  const [importing, setImporting] = useState(false);
  const dragDepth = useRef(0);

  const showError = (err: unknown) => {
    const issue = err instanceof ApiError ? err.issue : { code: "UNKNOWN", params: {} };
    toast.show(translateIssue(t, issue), issue.code === "UPLOAD_CANCELLED" ? "info" : "error");
  };

  const uploadFiles = (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      upload(file)
        .then((source) => toast.show(t("pages.library.imported", { name: source.name }), "success"))
        .catch(showError);
    }
  };

  // Ctrl+O opens the file picker.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "o") {
        e.preventDefault();
        fileInput.current?.click();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  };

  const submitLocation = async () => {
    const value = location.trim();
    if (!value) return;
    setImporting(true);
    try {
      const source = await importSource(value);
      replaceSource(source);
      setCurrentSourceId(source.id);
      setLocation("");
      toast.show(
        source.status === "downloading" ? t("pages.library.download_queued") : t("pages.library.imported", { name: source.name }),
        "success",
      );
    } catch (err) {
      showError(err);
    } finally {
      setImporting(false);
      void refresh();
    }
  };

  return (
    <section className={compact ? "mb-8 grid grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] gap-3" : "mb-8 space-y-3"}>
      <div
        data-testid="drop-zone"
        data-dragging={dragging}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragging(false);
        }}
        onDrop={onDrop}
        className={`flex items-center justify-center rounded-xl border-2 border-dashed transition-colors ${
          compact ? "gap-4 px-5 py-4 text-left" : "flex-col gap-3 px-8 py-14 text-center"
        } ${dragging ? "border-accent bg-accent/5" : "border-line bg-panel/50"}`}
      >
        <UploadIcon className={`shrink-0 ${compact ? "size-7" : "size-10"} ${dragging ? "text-accent" : "text-muted"}`} strokeWidth={1.5} />
        <div className={compact ? "min-w-0 flex-1" : "contents"}>
          <p className={compact ? "text-sm font-medium" : "text-base font-medium"}>{dragging ? t("pages.library.drop_active") : t("pages.library.drop")}</p>
          <p className={compact ? "mt-0.5 text-xs text-muted" : "max-w-2xl text-sm text-muted"}>
            {compact ? t("pages.library.drop_hint_short") : t("pages.library.drop_hint")}
          </p>
        </div>
        <Tooltip label={t("pages.library.choose_file")} shortcut="Ctrl+O">
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            data-testid="choose-file"
            className={`shrink-0 rounded-lg btn-primary px-4 py-2 text-sm ${compact ? "" : "mt-1"}`}
          >
            {t("pages.library.choose_file")}
          </button>
        </Tooltip>
        <input
          ref={fileInput}
          type="file"
          accept="video/*,.mkv,.mov,.webm,.m4v,.ts,.mts,.flv,.avi,.wmv"
          multiple
          hidden
          data-testid="file-input"
          onChange={(e) => {
            if (e.target.files?.length) uploadFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      <form
        className="rounded-xl border border-line bg-panel surface p-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submitLocation();
        }}
      >
        <label htmlFor="import-location" className="text-xs font-medium">
          {t("pages.library.location_label")}
        </label>
        <div className="mt-2 flex gap-2">
          <div className="relative min-w-0 flex-1">
            <Link2 className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <input
              id="import-location"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder={t("pages.library.location_placeholder")}
              data-testid="location-input"
              spellCheck={false}
              className="w-full rounded-lg border border-line bg-app py-2 pr-3 pl-9 font-mono text-xs text-fg outline-none placeholder:font-sans placeholder:text-muted/60 focus:border-accent"
            />
          </div>
          <Tooltip label={t("pages.library.import")} shortcut="Enter">
            <button
              type="submit"
              disabled={importing || !location.trim()}
              data-testid="import-button"
              className="rounded-lg btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
            >
              {importing ? t("common.loading") : t("pages.library.import")}
            </button>
          </Tooltip>
        </div>
        <p className="mt-2 text-xs text-muted">{t("pages.library.location_hint")}</p>
      </form>

      {uploads.map((item) => {
        const fraction = item.total ? item.loaded / item.total : 0;
        return (
          <div key={item.key} className={`rounded-xl border border-line bg-panel surface px-4 py-3 ${compact ? "col-span-2" : ""}`} data-testid="upload-row">
            <div className="flex items-center gap-3 text-sm">
              <UploadIcon className="size-4 shrink-0 text-accent" />
              <span className="shrink-0 text-muted">{t("pages.library.uploading")}</span>
              <span className="min-w-0 flex-1 truncate">{item.name}</span>
              <span className="shrink-0 font-mono text-xs text-muted">
                {formatBytes(item.loaded, locale)} / {formatBytes(item.total, locale)} · {formatPercent(fraction, locale)}
              </span>
              <Tooltip label={t("pages.library.cancel_upload")} side="bottom-end">
                <button
                  type="button"
                  onClick={item.abort}
                  aria-label={t("pages.library.cancel_upload")}
                  className="rounded-md p-1 text-muted hover:bg-white/5 hover:text-fg"
                >
                  <X className="size-4" />
                </button>
              </Tooltip>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${fraction * 100}%` }} />
            </div>
          </div>
        );
      })}
    </section>
  );
}
