export const UI_LANGUAGES = ["en", "vi"] as const;
export type UiLanguage = (typeof UI_LANGUAGES)[number];

export const LANGUAGE_STORAGE_KEY = "shortforge.uiLanguage";

export function isUiLanguage(value: unknown): value is UiLanguage {
  return typeof value === "string" && (UI_LANGUAGES as readonly string[]).includes(value);
}

/** Browser default: Vietnamese if the user's preferred language is Vietnamese, otherwise English. */
export function languageFromBrowser(languages: readonly string[]): UiLanguage {
  const preferred = (languages[0] ?? "").toLowerCase();
  return preferred === "vi" || preferred.startsWith("vi-") ? "vi" : "en";
}

function readStoredLanguage(): string | null {
  try {
    return localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function browserLanguages(): string[] {
  if (typeof navigator === "undefined") return [];
  if (navigator.languages?.length) return [...navigator.languages];
  return navigator.language ? [navigator.language] : [];
}

/** A saved choice wins; otherwise fall back to the browser language. */
export function detectInitialLanguage(
  stored: string | null = readStoredLanguage(),
  languages: readonly string[] = browserLanguages(),
): UiLanguage {
  return isUiLanguage(stored) ? stored : languageFromBrowser(languages);
}

export function saveLanguage(language: UiLanguage): void {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Private mode or storage disabled: the choice just won't persist.
  }
}
