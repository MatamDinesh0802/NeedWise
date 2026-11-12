import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Text colour pairs from globals.css must reach WCAG AA (4.5:1 for normal text) in both themes.
 * axe-core found two that didn't (e2e/browser/tests/a11y.spec.ts): muted text on --muted (avatar
 * initials, secondary badges) at 4.3:1, and the Restricted badge, white on amber-600, at 3.2:1.
 */
const css = readFileSync(fileURLToPath(new URL("./globals.css", import.meta.url)), "utf8");

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start, css.indexOf("\n}", start));
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

/** Linear sRGB of an oklch() or #rrggbb colour (Björn Ottosson's OKLab matrices). */
function linearRgb(color: string): [number, number, number] {
  const hex = /^#([0-9a-f]{6})$/i.exec(color);
  if (hex) {
    return [0, 2, 4].map((i) => {
      const c = parseInt(hex[1].slice(i, i + 2), 16) / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
  }
  const oklch = /^oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)\)$/.exec(color);
  if (!oklch) throw new Error(`unsupported colour ${color}`);
  const [L, C, h] = oklch.slice(1).map(Number);
  const a = C * Math.cos((h * Math.PI) / 180);
  const b = C * Math.sin((h * Math.PI) / 180);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return [
    clamp(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    clamp(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    clamp(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

function contrast(foreground: string, background: string): number {
  const luminance = (color: string) => {
    const [r, g, b] = linearRgb(color);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [light, dark] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

const TEXT_PAIRS: [foreground: string, background: string][] = [
  ["foreground", "background"],
  ["muted-foreground", "background"],
  ["muted-foreground", "muted"],
  ["muted-foreground", "card"],
  ["muted-foreground", "secondary"],
  ["primary-foreground", "primary"],
  ["secondary-foreground", "secondary"],
  ["warning-foreground", "warning"],
];

describe.each([":root", ".dark"])("design tokens in %s", (selector) => {
  const tokens = block(selector);

  it.each(TEXT_PAIRS)("%s on %s reaches 4.5:1", (foreground, background) => {
    expect(tokens[foreground], foreground).toBeDefined();
    expect(tokens[background], background).toBeDefined();
    expect(contrast(tokens[foreground], tokens[background])).toBeGreaterThanOrEqual(4.5);
  });
});

describe("the contrast helper", () => {
  it("matches the ratios axe-core reported before the fix", () => {
    expect(contrast("oklch(0.556 0 0)", "oklch(0.97 0 0)")).toBeCloseTo(4.34, 2); // #737373 on #f5f5f5
    expect(contrast("#ffffff", "oklch(0.666 0.179 58.318)")).toBeCloseTo(3.19, 2); // white on amber-600
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
  });
});
