import { Clapperboard, Download, Film, Settings, Sparkles, type LucideIcon } from "lucide-react";

export interface NavItem {
  path: string;
  labelKey: string;
  icon: LucideIcon;
  shortcut: string; // Alt + digit
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/library", labelKey: "nav.library", icon: Film, shortcut: "1" },
  { path: "/moments", labelKey: "nav.moments", icon: Sparkles, shortcut: "2" },
  { path: "/editor", labelKey: "nav.editor", icon: Clapperboard, shortcut: "3" },
  { path: "/export", labelKey: "nav.export", icon: Download, shortcut: "4" },
  { path: "/settings", labelKey: "nav.settings", icon: Settings, shortcut: "5" },
];

export const shortcutLabel = (digit: string) => `Alt+${digit}`;
