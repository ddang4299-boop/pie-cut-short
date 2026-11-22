import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./index.css";
import "./i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import App from "./App";
import { HealthProvider } from "./state/health";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <HealthProvider>
        <App />
      </HealthProvider>
    </BrowserRouter>
  </StrictMode>,
);
