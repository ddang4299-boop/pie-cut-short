import "@fontsource/be-vietnam-pro/600.css";
import "@fontsource/be-vietnam-pro/800.css";
import "@fontsource/be-vietnam-pro/900.css";

import { useEffect, useState } from "react";
import { useDelayRender } from "remotion";

/** Be Vietnam Pro: full Vietnamese diacritics (bundled locally, no network at render time). */
export const FONT_FAMILY = '"Be Vietnam Pro", "Inter", system-ui, sans-serif';
const WEIGHTS = [600, 800, 900];
const PROBE = "Aa Ếế Ộộ Đđ Ữữ";

/** Hold the render until the caption font (all weights, Vietnamese glyphs) is loaded. */
export function useFontsReady() {
  const { delayRender, continueRender } = useDelayRender();
  const [handle] = useState(() => delayRender("Loading Be Vietnam Pro"));
  useEffect(() => {
    if (typeof document === "undefined" || !document.fonts) {
      continueRender(handle);
      return;
    }
    Promise.all(WEIGHTS.map((w) => document.fonts.load(`${w} 40px "Be Vietnam Pro"`, PROBE)))
      .catch(() => undefined)
      .finally(() => continueRender(handle));
  }, [handle, continueRender]);
}
