import type { ReactNode } from "react";

type Side = "right" | "bottom" | "bottom-end" | "top";

const SIDE_CLASSES: Record<Side, string> = {
  right: "left-full top-1/2 ml-2 -translate-y-1/2",
  bottom: "left-1/2 top-full mt-2 -translate-x-1/2",
  "bottom-end": "right-0 top-full mt-2",
  top: "bottom-full left-1/2 mb-2 -translate-x-1/2",
};

/** Hover/focus tooltip. Every button gets one; `shortcut` shows its keyboard shortcut. */
export function Tooltip({
  label,
  shortcut,
  side = "bottom",
  children,
}: {
  label: string;
  shortcut?: string;
  side?: Side;
  children: ReactNode;
}) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span
        role="tooltip"
        className={`pointer-events-none absolute z-50 whitespace-nowrap rounded-md border border-line bg-panel px-2 py-1 text-xs text-fg opacity-0 shadow-lg shadow-black/40 transition-opacity duration-150 group-hover/tip:opacity-100 group-hover/tip:delay-300 group-focus-within/tip:opacity-100 ${SIDE_CLASSES[side]}`}
      >
        {label}
        {shortcut && <kbd className="ml-2 font-mono text-[11px] text-muted">{shortcut}</kbd>}
      </span>
    </span>
  );
}
