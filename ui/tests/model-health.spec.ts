import { test, expect } from '@playwright/test';

const health = {
  default_model: 'medium', scope: 'Local files only', models: [
    { size: 'small', status: 'download_required', source: 'huggingface_cache', message: 'Downloads on first use.' },
    { size: 'medium', status: 'available', source: 'configured', message: 'Files are readable.' },
    { size: 'large', status: 'unavailable', source: 'configured', message: 'Configured weights not found: ' + 'long-path/'.repeat(40) },
  ],
};

test('mobile model health shows availability and handles refresh failures', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  let failing = false;
  await page.route('**/api/jobs?*', route => route.fulfill({ json: [] }));
  await page.route('**/api/health/models', route => failing
    ? route.fulfill({ status: 503, json: { detail: 'temporarily unavailable' } })
    : route.fulfill({ json: health }));
  await page.goto('/');
  const panel = page.getByRole('region', { name: 'Model availability', exact: true });
  await expect(panel.getByText('Medium · selected')).toBeVisible();
  await expect(panel.getByText('Available', { exact: true })).toBeVisible();
  await expect(panel.getByText('Needs download', { exact: true })).toBeVisible();
  await expect(panel.getByText('Unavailable', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.getByLabel('Model', { exact: true }).selectOption('small');
  await expect(panel.getByText('Small · selected')).toBeVisible();
  failing = true;
  await panel.getByRole('button', { name: 'Refresh model availability' }).click();
  await expect(panel.getByText('Could not check model availability. Try refreshing.')).toBeVisible();
  await expect(panel.getByText('Available', { exact: true })).toHaveCount(0);
  failing = false;
  await panel.getByRole('button', { name: 'Refresh model availability' }).click();
  await expect(panel.getByText('Available', { exact: true })).toBeVisible();
});
