import { useEffect, useRef, useState, type ReactNode } from "react";

export interface MenuItem {
  key: string;
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  icon?: ReactNode;
}

export interface MenuSection {
  key: string;
  title?: string;
  items: MenuItem[];
}

/** Dropdown menu anchored to its trigger. Closes on outside click, Esc or selection. */
export function Menu({
  trigger,
  sections,
  align = "right",
  testId,
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode;
  sections: MenuSection[];
  align?: "left" | "right";
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative inline-flex">
      {trigger({ open, toggle: () => setOpen((v) => !v) })}
      {open && (
        <div
          role="menu"
          data-testid={testId}
          className={`absolute top-full z-30 mt-1 min-w-56 rounded-lg border border-line bg-panel surface py-1 shadow-xl shadow-black/50 ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {sections.map((section, i) => (
            <div key={section.key} className={i > 0 ? "mt-1 border-t border-line pt-1" : undefined}>
              {section.title && <div className="px-3 py-1 text-xs uppercase tracking-wide text-muted">{section.title}</div>}
              {section.items.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  role="menuitem"
                  disabled={item.disabled}
                  onClick={() => {
                    setOpen(false);
                    item.onSelect();
                  }}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40 ${
                    item.danger ? "text-danger" : "text-fg"
                  }`}
                >
                  {item.icon}
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
