import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests against a running, seeded stack (`make up seed`, then
 * `make e2e-browser`). E2E_BASE_URL picks the stack; E2E_MODEL=1 also runs the tests tagged
 * @model, which need a working chat model (CI has none: hash embeddings, no API key).
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:8480";
const withModel = process.env.E2E_MODEL === "1";
const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  // Tests only create data with the "E2E browser" prefix and delete it, so files can run in
  // parallel; tests inside a file share a signed-in user and run in order.
  fullyParallel: false,
  workers: ci ? 2 : 3,
  retries: ci ? 1 : 0,
  forbidOnly: ci,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  grepInvert: withModel ? undefined : /@model/,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    locale: "en-US",
    timezoneId: "UTC",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } }],
});
