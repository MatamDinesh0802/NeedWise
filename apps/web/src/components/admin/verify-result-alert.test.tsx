import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { VerifyResultAlert } from "./verify-result-alert";

describe("VerifyResultAlert", () => {
  it("renders an intact chain as a non-destructive alert with the count", () => {
    const html = renderToStaticMarkup(<VerifyResultAlert result={{ ok: true, checked: 12, first_broken_seq: null }} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain('data-tone="ok"');
    expect(html).toContain("Chain intact");
    expect(html).toContain("12 events checked");
    expect(html).not.toContain("Tampering");
  });

  it("renders tampering clearly, naming the event", () => {
    const html = renderToStaticMarkup(<VerifyResultAlert result={{ ok: false, checked: 30, first_broken_seq: 17 }} />);
    expect(html).toContain('data-tone="danger"');
    expect(html).toContain("Tampering detected at event #17");
    expect(html).toContain("text-destructive"); // destructive alert variant
    expect(html).not.toContain("Chain intact");
  });
});
