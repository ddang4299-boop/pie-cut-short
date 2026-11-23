import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

type ToastKind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastApi {
  show: (message: string, kind?: ToastKind) => void;
}

const ToastContext = createContext<ToastApi | null>(null);
const DURATION_MS: Record<ToastKind, number> = { success: 3500, info: 4000, error: 7000 };
const ICONS = { success: CheckCircle2, error: AlertTriangle, info: Info };
const COLORS: Record<ToastKind, string> = { success: "text-success", error: "text-danger", info: "text-muted" };

/** Small notices in the bottom-right corner (normal notices; destructive actions use ConfirmDialog). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((prev) => prev.filter((toast) => toast.id !== id)), []);
  const show = useCallback(
    (message: string, kind: ToastKind = "info") => {
      const id = nextId.current++;
      setToasts((prev) => [...prev.slice(-3), { id, kind, message }]);
      window.setTimeout(() => dismiss(id), DURATION_MS[kind]);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-96 flex-col gap-2" aria-live="polite">
        {toasts.map((toast) => {
          const Icon = ICONS[toast.kind];
          return (
            <div
              key={toast.id}
              role={toast.kind === "error" ? "alert" : "status"}
              data-testid={`toast-${toast.kind}`}
              className="pointer-events-auto flex items-start gap-3 rounded-lg border border-line bg-panel surface px-4 py-3 text-sm shadow-xl shadow-black/50"
            >
              <Icon className={`mt-0.5 size-4 shrink-0 ${COLORS[toast.kind]}`} />
              <p className="min-w-0 flex-1 break-words">{toast.message}</p>
              <button
                type="button"
                onClick={() => dismiss(toast.id)}
                aria-label={t("common.close")}
                title={t("common.close")}
                className="rounded p-0.5 text-muted hover:text-fg"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
