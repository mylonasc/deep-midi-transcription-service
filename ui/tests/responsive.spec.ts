import { test, expect } from '@playwright/test';
import { mockModelHealth } from './health-fixture';

test.beforeEach(async ({ page }) => mockModelHealth(page));

const longTitle = 'An unusually long song title / ' + 'UnbrokenTitle'.repeat(45);
const longUrl = 'https://www.youtube.com/watch?v=' + 'query'.repeat(90);
const longError = 'RuntimeError: stage failed\n' + 'VeryLongUnbrokenDiagnostic'.repeat(120);
const fixture = (status: string, id: string) => ({
  id, title: longTitle, youtube_url: longUrl, status, retry_count: 2,
  config: { transcription: { model: 'medium' } },
  progress: { download_progress: 0.33, chunks_completed: 4, chunks_total: 25 },
  error: status === 'failed' ? longError : null,
  audio_key: status === 'queued' ? null : 'source.m4a', midi_key: null, events_key: null,
});

for (const width of [320, 375, 768, 1280]) {
  test(`no overflow or inaccessible controls at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/jobs?*', route => route.fulfill({ json: [
      fixture('failed', 'failed-job'), fixture('transcribing', 'active-job'), fixture('queued', 'queued-job'),
    ] }));
    await page.goto('/');
    await expect(page.getByTestId('job-card')).toHaveCount(3);
    await page.getByText('Error details', { exact: true }).click();
    await expect(page.locator('.error-details pre')).toHaveText(longError);

    const problems = await page.evaluate(() => {
      const issues: string[] = [];
      const viewport = document.documentElement.clientWidth;
      if (document.documentElement.scrollWidth > viewport + 1) issues.push('horizontal page overflow');
      document.querySelectorAll('main *, header *').forEach(element => {
        const el = element as HTMLElement;
        const bounds = el.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        if (bounds.left < -1 || bounds.right > viewport + 1) issues.push(`outside viewport: ${el.tagName}.${el.className}`);
      });
      document.querySelectorAll('.job-card, .panel, .notice, .error-details pre').forEach(element => {
        const el = element as HTMLElement;
        if (el.scrollWidth > el.clientWidth + 1) issues.push(`content overflow: ${el.className}`);
      });
      document.querySelectorAll('button, .button-link, select, input:not([type=checkbox]), summary, .checkbox-label').forEach(element => {
        const el = element as HTMLElement;
        const bounds = el.getBoundingClientRect();
        if (bounds.height && bounds.height < 44) issues.push(`small touch target: ${el.tagName} ${el.textContent}`);
      });
      document.querySelectorAll('input, select').forEach(element => {
        if (!(element as HTMLInputElement).labels?.length && !element.getAttribute('aria-label')) issues.push('unlabelled form control');
      });
      return issues;
    });
    expect(problems).toEqual([]);
    expect(errors).toEqual([]);
    await page.getByLabel('Show', { exact: true }).selectOption('failed');
    await expect(page.getByTestId('job-card')).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
  });
}

test('mobile retry queues the same job and preserves downloaded artifacts', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  let job = fixture('failed', 'retry-job');
  let retries = 0;
  await page.route('**/api/jobs?*', route => route.fulfill({ json: [job] }));
  await page.route('**/api/jobs/retry-job/retry', async route => {
    expect(route.request().method()).toBe('POST');
    retries++;
    job = { ...job, status: 'queued', error: null, retry_count: 3 };
    await route.fulfill({ status: 202, json: job });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  const card = page.getByTestId('job-card');
  await expect(card.locator('.status')).toHaveText('Queued');
  await expect(card.getByText('Attempt 4')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Retry' })).toHaveCount(0);
  await expect(card.getByRole('button', { name: 'Cancel', exact: true })).toBeVisible();
  await expect(card.getByRole('link', { name: 'Open audio' })).toBeVisible();
  expect(retries).toBe(1);
});

test('retry errors remain visible and empty states are useful', async ({ page }) => {
  await page.route('**/api/jobs?*', route => route.fulfill({ json: [fixture('failed', 'failed-job')] }));
  await page.route('**/api/jobs/failed-job/retry', route => route.fulfill({ status: 409, json: { detail: 'Job is already queued' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Job is already queued');
  await page.getByRole('button', { name: 'Dismiss' }).click();
  await page.getByLabel('Show', { exact: true }).selectOption('done');
  await expect(page.getByText('No matching jobs', { exact: true })).toBeVisible();
});
