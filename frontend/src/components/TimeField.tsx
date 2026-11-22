import { useEffect, useRef, useState } from "react";

import { formatTimecode, parseTimecode } from "../lib/format";

/**
 * A timecode field (mm:ss.ff). Type a value and press Enter, or drag horizontally to scrub
 * (0.05 s per pixel, Shift = 0.01 s per pixel). Esc cancels typing.
 */
export function TimeField({
  value,
  onChange,
  onScrub,
  min = 0,
  max = Infinity,
  label,
  testId,
  className = "",
}: {
  value: number;
  onChange: (seconds: number) => void;
  /** Live updates while dragging (onChange fires once when the drag ends). */
  onScrub?: (seconds: number) => void;
  min?: number;
  max?: number;
  label: string;
  testId?: string;
  className?: string;
}) {
  const [text, setText] = useState(formatTimecode(value));
  const [editing, setEditing] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const drag = useRef<{ x: number; start: number; moved: boolean; last: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setText(formatTimecode(value));
  }, [value, editing]);

  const clamp = (t: number) => Math.round(Math.min(max, Math.max(min, t)) * 1000) / 1000;

  const commit = () => {
    const parsed = parseTimecode(text);
    if (parsed == null) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setEditing(false);
    const next = clamp(parsed);
    setText(formatTimecode(next));
    if (Math.abs(next - value) > 1e-4) onChange(next);
  };

  return (
    <input
      ref={input}
      aria-label={label}
      data-testid={testId}
      value={text}
      readOnly={!editing}
      onChange={(e) => {
        setText(e.target.value);
        setInvalid(false);
      }}
      onPointerDown={(e) => {
        if (editing || e.button !== 0) return;
        e.preventDefault();
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, start: value, moved: false, last: value };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const dx = e.clientX - d.x;
        if (!d.moved && Math.abs(dx) < 3) return;
        d.moved = true;
        d.last = clamp(d.start + dx * (e.shiftKey ? 0.01 : 0.05));
        setText(formatTimecode(d.last));
        onScrub?.(d.last);
      }}
      onPointerUp={() => {
        const d = drag.current;
        drag.current = null;
        if (!d) return;
        if (d.moved) {
          if (Math.abs(d.last - value) > 1e-4) onChange(d.last);
        } else {
          setEditing(true);
          window.setTimeout(() => input.current?.select(), 0);
        }
      }}
      onBlur={() => editing && commit()}
      onKeyDown={(e) => {
        e.stopPropagation(); // page shortcuts must not fire while typing
        if (!editing) {
          if (e.key === "Enter") {
            setEditing(true);
            window.setTimeout(() => input.current?.select(), 0);
          }
          return;
        }
        if (e.key === "Enter") commit();
        if (e.key === "Escape") {
          setEditing(false);
          setInvalid(false);
          setText(formatTimecode(value));
        }
      }}
      className={`w-24 rounded-md border bg-app px-2 py-1 text-center font-mono text-xs outline-none ${
        editing ? "cursor-text border-accent" : "cursor-ew-resize border-line hover:border-muted"
      } ${invalid ? "border-danger text-danger" : "text-fg"} ${className}`}
    />
  );
}
