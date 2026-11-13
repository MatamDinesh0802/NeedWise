import { defineConfig, devices } from "@playwright/test";

/**
 * Records the demo video (README.md in this folder): one test, one browser, against a running,
 * seeded stack with a chat model. Not part of the browser suite (`make e2e-browser`).
 */
export default defineConfig({
  testDir: ".",
  testMatch: "record.spec.ts",
  outputDir: "./out/test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20 * 60_000,
  expect: { timeout: 30_000 },
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8480",
    viewport: { width: 1280, height: 720 },
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    trace: "off",
    video: "off", // the test records its own context, with the overlay
  },
});
