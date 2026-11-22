import { describe, expect, it } from "vitest";

import en from "./en.json";
import vi from "./vi.json";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[full] = value;
    else Object.assign(out, flatten(value, full));
  }
  return out;
}

const placeholders = (s: string) => [...s.matchAll(/\{\{\s*([a-zA-Z0-9_]+)/g)].map((m) => m[1]).sort();

describe("locale files", () => {
  const enFlat = flatten(en as Tree);
  const viFlat = flatten(vi as Tree);

  it("en.json and vi.json have exactly the same keys", () => {
    expect(Object.keys(viFlat).sort()).toEqual(Object.keys(enFlat).sort());
  });

  it("every value is a non-empty string", () => {
    for (const [key, value] of [...Object.entries(enFlat), ...Object.entries(viFlat)]) {
      expect(value.trim(), key).not.toBe("");
    }
  });

  it("each key uses the same interpolation placeholders in both languages", () => {
    for (const key of Object.keys(enFlat)) {
      expect(placeholders(viFlat[key] ?? ""), key).toEqual(placeholders(enFlat[key]));
    }
  });
});
