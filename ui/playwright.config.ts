import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://127.0.0.1:5197',
    launchOptions: { executablePath: process.env.CHROME_BIN || undefined,
      args: ['--autoplay-policy=no-user-gesture-required'] } },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port 5197 --strictPort', port: 5197, reuseExistingServer: false },
});
