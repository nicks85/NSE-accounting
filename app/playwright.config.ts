import { defineConfig, devices } from "@playwright/test";

/** KOSH_E2E_PORT runs the suite on another port, e.g. when a stale preview holds 4173. */
const port = Number(process.env.KOSH_E2E_PORT ?? 4173);

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: `http://localhost:${port}` },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `pnpm exec vite preview --port ${port} --strictPort`,
    url: `http://localhost:${port}`,
    reuseExistingServer: !process.env.CI,
  },
});
