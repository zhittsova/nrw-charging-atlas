import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.pw.ts",
  timeout: 60_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:4173", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } }
  ],
  webServer: {
    command: "python3 ../scripts/serve_public_site.py",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false
  }
});
