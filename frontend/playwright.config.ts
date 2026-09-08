import { defineConfig, devices } from '@playwright/test'

/**
 * The browser tests run against a server the caller already started, because
 * the backend owns the database and sample fixtures. Point BASE_URL at it.
 */
const baseURL = process.env.BASE_URL ?? 'http://127.0.0.1:8000'

// Set these when the target has reviewer credentials configured, as the
// container does. Local development runs open by default and needs neither.
const httpCredentials = process.env.DEMO_USERNAME
  ? { username: process.env.DEMO_USERNAME, password: process.env.DEMO_PASSWORD ?? '' }
  : undefined

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    httpCredentials,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
