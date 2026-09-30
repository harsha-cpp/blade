import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { headless: true },
  webServer: process.env["BLADE_LANDING_URL"]
    ? []
    : {
        command:
          "npm --prefix ../../../landing run dev -- --hostname 127.0.0.1 --port 3100",
        url: "http://127.0.0.1:3100",
        reuseExistingServer: !process.env["CI"],
        timeout: 120_000,
      },
  projects: [
    {
      name: "chromium-extension",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
