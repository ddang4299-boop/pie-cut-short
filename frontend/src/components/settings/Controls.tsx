import { useEffect, useState, type ReactNode } from "react";

export const selectCls =
  "w-full min-w-0 rounded-md border border-line bg-app px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-50";
export const inputCls = selectCls;

/** A Settings card: title, one-line hint, body. */
export function Card({ title, hint, actions, children, testId }: { title: string; hint?: string; actions?: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <section className="rounded-xl border border-line bg-panel p-5" data-testid={testId}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  );
}

/** Label on the left (fixed width, wraps for longer Vietnamese labels), control on the right. */
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[240px_minmax(0,1fr)] items-start gap-4">
      <div className="pt-1.5 text-sm">
        {label}
        {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** Number field that commits on blur / Enter (clamped), so typing "1" on the way to "15" saves nothing. */
export function NumberInput({
  value,
  min,
  max,
  step = 1,
  onCommit,
  testId,
  label,
  className = "",
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onCommit: (v: number) => void;
  testId?: string;
  label?: string;
  className?: string;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const n = Number(text.replace(",", "."));
    if (!Number.isFinite(n)) return setText(String(value));
    const clamped = Math.min(max, Math.max(min, n));
    setText(String(clamped));
    if (clamped !== value) onCommit(clamped);
  };
  return (
    <input
      type="text"
      inputMode="decimal"
      aria-label={label}
      data-testid={testId}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const n = Math.min(max, Math.max(min, Math.round((value + (e.key === "ArrowUp" ? step : -step)) * 1000) / 1000));
          if (n !== value) onCommit(n);
        }
      }}
      className={`${inputCls} w-24 tabular-nums ${className}`}
    />
  );
}

/** Text field that commits on blur / Enter. */
export function TextInput({ value, onCommit, placeholder, testId, label, maxLength = 500 }: {
  value: string;
  onCommit: (v: string) => void;
  placeholder?: string;
  testId?: string;
  label?: string;
  maxLength?: number;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <input
      type="text"
      aria-label={label}
      data-testid={testId}
      value={text}
      maxLength={maxLength}
      placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text.trim() !== value && onCommit(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      className={inputCls}
    />
  );
}

export function Toggle({ checked, onChange, label, testId }: { checked: boolean; onChange: (v: boolean) => void; label: string; testId?: string }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <input type="checkbox" className="size-4 accent-[#FF3D7F]" checked={checked} data-testid={testId} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
