import { Scissors } from "lucide-react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";

import { NAV_ITEMS, shortcutLabel } from "./nav";
import { Tooltip } from "./Tooltip";

/** 64px icon-only sidebar with tooltips. */
export function Sidebar() {
  const { t } = useTranslation();
  return (
    <nav aria-label={t("nav.main")} className="flex w-16 shrink-0 flex-col items-center gap-2 border-r border-line bg-panel py-3">
      <Tooltip label={t("app.title")} side="right">
        <div role="img" aria-label={t("app.title")} className="mb-3 flex size-10 items-center justify-center rounded-xl bg-accent text-white">
          <Scissors className="size-5" strokeWidth={2.25} />
        </div>
      </Tooltip>
      {NAV_ITEMS.map(({ path, labelKey, icon: Icon, shortcut }) => (
        <Tooltip key={path} label={t(labelKey)} shortcut={shortcutLabel(shortcut)} side="right">
          <NavLink
            to={path}
            aria-label={t(labelKey)}
            data-testid={`nav-${path.slice(1)}`}
            className={({ isActive }) =>
              `relative flex size-11 items-center justify-center rounded-xl transition-colors ${
                isActive ? "bg-accent/15 text-accent" : "text-muted hover:bg-white/5 hover:text-fg"
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && <span className="absolute -left-2.5 h-6 w-1 rounded-r bg-accent" />}
                <Icon className="size-5" />
              </>
            )}
          </NavLink>
        </Tooltip>
      ))}
    </nav>
  );
}
