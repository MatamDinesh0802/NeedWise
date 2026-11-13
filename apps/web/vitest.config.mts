import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Dates render in the runtime's locale and time zone (lib/format.ts), as they do in a browser.
// Pin both for the tests, so a developer's laptop and CI (en-US, UTC) agree. The test workers
// are separate processes that read these at startup.
process.env.TZ = "UTC";
process.env.LANG = "en_GB.UTF-8";
process.env.LC_ALL = "en_GB.UTF-8";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
