import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";

import { Logo } from "./Logo";
import { NAV_ITEMS, shortcutLabel, type NavItem } from "./nav";
import { Tooltip } from "./Tooltip";

/** Sidebar: the four workflow steps (numbered, with labels), then Guide and Settings at the bottom. */
export function Sidebar() {
  const { t } = useTranslation();
  const steps = NAV_ITEMS.filter((n) => !n.bottom);
  const bottom = NAV_ITEMS.filter((n) => n.bottom);
  return (
    <nav aria-label={t("nav.main")} className="flex w-[92px] shrink-0 flex-col items-center border-r border-line bg-app py-3">
      <Tooltip label={t("app.title")} side="right">
        <div className="mb-4 flex size-11 items-center justify-center" data-testid="app-logo">
          <Logo className="size-11 drop-shadow-[0_6px_14px_rgba(124,92,255,0.35)]" title={t("app.title")} />
        </div>
      </Tooltip>
      <div className="flex w-full flex-col items-center gap-1">
        {steps.map((item, i) => (
          <div key={item.path} className="flex w-full flex-col items-center">
            <SidebarLink item={item} />
            {i < steps.length - 1 && <span aria-hidden className="h-2 w-px bg-line-strong" />}
          </div>
        ))}
      </div>
      <div className="mt-auto flex w-full flex-col items-center gap-1 border-t border-line pt-3">
        {bottom.map((item) => (
          <SidebarLink key={item.path} item={item} />
        ))}
      </div>
    </nav>
  );
}

function SidebarLink({ item }: { item: NavItem }) {
  const { t } = useTranslation();
  const { path, labelKey, icon: Icon, shortcut, step } = item;
  return (
    <Tooltip label={t(labelKey)} shortcut={shortcutLabel(shortcut)} side="right">
      <NavLink
        to={path}
        aria-label={t(labelKey)}
        data-testid={`nav-${path.slice(1)}`}
        className={({ isActive }) =>
          `relative flex w-[80px] flex-col items-center gap-1 rounded-xl px-1 py-2 transition-colors ${
            isActive ? "bg-white/[0.09] text-fg shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" : "text-muted hover:bg-white/5 hover:text-fg"
          }`
        }
      >
        {({ isActive }) => (
          <>
            {isActive && <span className="absolute top-1/2 -left-1.5 h-7 w-1 -translate-y-1/2 rounded-r bg-accent shadow-[0_0_10px_var(--color-accent)]" />}
            <span className="relative">
              <Icon className={`size-5 ${isActive ? "text-fg" : ""}`} />
              {step && (
                <span
                  className={`absolute -top-1.5 -right-2.5 flex size-4 items-center justify-center rounded-full text-[10px] leading-none font-semibold ${
                    isActive ? "bg-accent text-white" : "bg-white/12 text-fg/80"
                  }`}
                >
                  {step}
                </span>
              )}
            </span>
            <span className="w-full text-center text-[11.5px] leading-tight font-medium">{t(labelKey)}</span>
          </>
        )}
      </NavLink>
    </Tooltip>
  );
}
