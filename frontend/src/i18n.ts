import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { patchSettings } from "./lib/api";

import { detectInitialLanguage, isUiLanguage, saveLanguage, UI_LANGUAGES } from "./lib/language";
import en from "./locales/en.json";
import vi from "./locales/vi.json";

export const resources = {
  en: { translation: en },
  vi: { translation: vi },
} as const;

void i18n.use(initReactI18next).init({
  resources,
  lng: detectInitialLanguage(),
  fallbackLng: "en",
  supportedLngs: [...UI_LANGUAGES],
  interpolation: { escapeValue: false }, // React already escapes
});

function applyToDocument(language: string): void {
  document.documentElement.lang = language;
  document.title = i18n.t("app.title");
}

applyToDocument(i18n.language);

/** The backend needs the UI language too: moment `reason` texts are written in it. */
function syncBackend(language: string): void {
  if (isUiLanguage(language)) patchSettings({ ui_language: language }).catch(() => undefined);
}

i18n.on("languageChanged", (language) => {
  if (isUiLanguage(language)) saveLanguage(language);
  applyToDocument(language);
  syncBackend(language);
});

if (typeof window !== "undefined" && typeof fetch !== "undefined") syncBackend(i18n.language);

export default i18n;
