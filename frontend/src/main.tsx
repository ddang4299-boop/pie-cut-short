import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/instrument-serif/400.css";
import "./index.css";
import "./i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import App from "./App";
import { ToastProvider } from "./components/Toasts";
import { HealthProvider } from "./state/health";
import { SaveStatusProvider } from "./state/save";
import { SourcesProvider } from "./state/sources";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <HealthProvider>
        <SourcesProvider>
          <SaveStatusProvider>
            <ToastProvider>
              <App />
            </ToastProvider>
          </SaveStatusProvider>
        </SourcesProvider>
      </HealthProvider>
    </BrowserRouter>
  </StrictMode>,
);
