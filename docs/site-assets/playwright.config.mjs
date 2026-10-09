import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './browser',
  testMatch: '*.spec.mjs',
  workers: 1,
  retries: 0,
  timeout: 30000,
  use: { browserName: 'chromium', serviceWorkers: 'block' },
  outputDir: '../../tmp/browser-results',
  reporter: 'list',
});
