import { useTranslation } from "react-i18next";

import { UI_LANGUAGES } from "../lib/language";
import { Tooltip } from "./Tooltip";

/** EN / VI segmented switch. `full` shows the language names (used in Settings). */
export function LanguageSwitcher({ variant = "compact" }: { variant?: "compact" | "full" }) {
  const { t, i18n } = useTranslation();
  const current = i18n.resolvedLanguage ?? i18n.language;
  const full = variant === "full";

  return (
    <div
      role="radiogroup"
      aria-label={t("header.language")}
      className="inline-flex rounded-lg border border-line bg-app p-0.5"
    >
      {UI_LANGUAGES.map((language) => {
        const active = current === language;
        return (
          <Tooltip key={language} label={t(`language.${language}`)} side={full ? "top" : "bottom-end"}>
            <button
              type="button"
              role="radio"
              aria-checked={active}
              data-testid={`lang-${full ? "full-" : ""}${language}`}
              onClick={() => void i18n.changeLanguage(language)}
              className={`rounded-md font-medium transition-colors ${full ? "px-4 py-2 text-sm" : "px-2.5 py-1 text-xs"} ${
                active ? "bg-accent text-white" : "text-muted hover:text-fg"
              }`}
            >
              {full ? t(`language.${language}`) : t(`language.${language}_short`)}
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
