import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"], ["json", { outputFile: "e2e/results.json" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost:8443",
    ignoreHTTPSErrors: process.env.E2E_IGNORE_TLS === "true",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
