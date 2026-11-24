import { BookOpen, Clapperboard, Download, Film, Settings, Sparkles, type LucideIcon } from "lucide-react";

export interface NavItem {
  path: string;
  labelKey: string;
  icon: LucideIcon;
  shortcut: string; // Alt + digit
  step?: number; // position in the workflow (Library 1 → Export 4); shown as a badge
  bottom?: boolean; // pinned to the bottom of the sidebar
}

export const NAV_ITEMS: NavItem[] = [
  { path: "/library", labelKey: "nav.library", icon: Film, shortcut: "1", step: 1 },
  { path: "/moments", labelKey: "nav.moments", icon: Sparkles, shortcut: "2", step: 2 },
  { path: "/editor", labelKey: "nav.editor", icon: Clapperboard, shortcut: "3", step: 3 },
  { path: "/export", labelKey: "nav.export", icon: Download, shortcut: "4", step: 4 },
  { path: "/guide", labelKey: "nav.guide", icon: BookOpen, shortcut: "6", bottom: true },
  { path: "/settings", labelKey: "nav.settings", icon: Settings, shortcut: "5", bottom: true },
];

export const shortcutLabel = (digit: string) => `Alt+${digit}`;
