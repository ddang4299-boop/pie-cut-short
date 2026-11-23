import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Tooltip } from "./Tooltip";

/** Modal for destructive or credit-spending actions. Enter confirms, Esc cancels. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={onCancel}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid="confirm-dialog"
        className="w-full max-w-md rounded-xl border border-line bg-panel surface p-5 shadow-2xl shadow-black/60"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">{title}</h2>
        {children && <div className="mt-3 space-y-3 text-sm text-muted">{children}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <Tooltip label={t("common.cancel")} shortcut="Esc">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg border border-line px-4 py-2 text-sm hover:bg-white/5"
            >
              {t("common.cancel")}
            </button>
          </Tooltip>
          <Tooltip label={confirmLabel} shortcut="Enter">
            <button
              ref={confirmRef}
              type="button"
              disabled={busy}
              onClick={onConfirm}
              data-testid="confirm-button"
              className={`rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 ${
                danger ? "bg-danger text-white hover:bg-danger/90" : "btn-primary"
              }`}
            >
              {confirmLabel}
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
