import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Side = "right" | "bottom" | "bottom-end" | "top";

const GAP = 8; // px between the anchor and the tooltip
const EDGE = 8; // px kept clear of the window edges
const HOVER_DELAY = 300; // ms

/** Where the tooltip goes for its side, clamped inside the window. */
function place(side: Side, a: DOMRect, w: number, h: number) {
  let x: number;
  let y: number;
  if (side === "right") {
    x = a.right + GAP;
    y = a.top + a.height / 2 - h / 2;
  } else if (side === "top") {
    x = a.left + a.width / 2 - w / 2;
    y = a.top - GAP - h;
    if (y < EDGE) y = a.bottom + GAP; // no room above: flip below
  } else {
    x = side === "bottom-end" ? a.right - w : a.left + a.width / 2 - w / 2;
    y = a.bottom + GAP;
    if (y + h > window.innerHeight - EDGE) y = a.top - GAP - h; // no room below: flip above
  }
  x = Math.min(Math.max(EDGE, x), window.innerWidth - w - EDGE);
  y = Math.min(Math.max(EDGE, y), window.innerHeight - h - EDGE);
  return { x, y };
}

/**
 * Hover/focus tooltip. Every button gets one; `shortcut` shows its keyboard shortcut.
 * The visible bubble is portaled to <body> with fixed positioning, so it never widens a scroll
 * container or gets clipped by one. The label also stays in place as screen-reader text.
 */
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
  const anchor = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const show = (delay: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(true), delay);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    setOpen(false);
    setPos(null);
  };

  // measure the bubble, then place it (before paint, so it never flashes at the wrong spot)
  useLayoutEffect(() => {
    if (!open || !anchor.current || !bubble.current) return;
    const b = bubble.current.getBoundingClientRect();
    setPos(
      place(side, anchor.current.getBoundingClientRect(), b.width, b.height),
    );
  }, [open, side, label, shortcut]);

  useLayoutEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <span
      ref={anchor}
      className="relative inline-flex"
      onMouseEnter={() => show(HOVER_DELAY)}
      onMouseLeave={hide}
      onFocus={(e) => {
        // keyboard focus only; a mouse click focuses too but should not pop the tooltip
        if ((e.target as HTMLElement).matches?.(":focus-visible")) show(0);
      }}
      onBlur={hide}
      onMouseDown={hide}
    >
      {children}
      <span className="sr-only">
        {label}
        {shortcut && ` (${shortcut})`}
      </span>
      {open &&
        createPortal(
          <span
            ref={bubble}
            role="tooltip"
            style={{
              left: pos?.x ?? 0,
              top: pos?.y ?? 0,
              visibility: pos ? "visible" : "hidden",
            }}
            className="pointer-events-none fixed z-[100] whitespace-nowrap rounded-md border border-line bg-panel px-2 py-1 text-xs text-fg shadow-lg shadow-black/40"
          >
            {label}
            {shortcut && (
              <kbd className="ml-2 font-mono text-[11px] text-muted">
                {shortcut}
              </kbd>
            )}
          </span>,
          document.body,
        )}
    </span>
  );
}
