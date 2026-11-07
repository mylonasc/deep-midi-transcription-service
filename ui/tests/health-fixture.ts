import type { Page } from '@playwright/test';

export async function mockModelHealth(page: Page) {
  await page.route('**/api/health/models', route => route.fulfill({ json: {
    default_model: 'medium', scope: 'Local files only', models: ['small', 'medium', 'large'].map(size => ({
      size, status: 'available', source: 'configured', message: 'Weights are readable.',
    })),
  } }));
}
