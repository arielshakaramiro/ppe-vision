import { defineConfig, devices } from "@playwright/test";

/**
 * E2E APD Vision. Server dijalankan oleh e2e/run-server.sh: menyiapkan database
 * sementara berisi data contoh + tiga akun peran, lalu menyajikan frontend hasil
 * build lewat uvicorn di port 8099. Semua lokal, tanpa internet.
 */
const PORT = 8099;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.results",
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI
    ? [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : [["list"]],
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    locale: "id-ID",
    timezoneId: "Asia/Jakarta",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "bash e2e/run-server.sh",
    url: `http://127.0.0.1:${PORT}/api/healthz`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
});
