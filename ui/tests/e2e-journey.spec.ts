import { test, expect, type Page } from '@playwright/test';
import midiPackage from '@tonejs/midi';
import { mockModelHealth } from './health-fixture';
import { devices, type DeviceProfile } from './devices';

async function auditLayout(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const issues: string[] = [];
    const viewport = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > viewport + 1) issues.push('horizontal page overflow');
    document.querySelectorAll('main *, header *').forEach(element => {
      const el = element as HTMLElement;
      // Scroll containers legitimately hold wider notation/roll content.
      if (el.closest('.notation-scroll, .piano-roll-scroll')) return;
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
      if (bounds.height && bounds.height < 44) issues.push(`small touch target: ${el.tagName} ${el.textContent?.slice(0, 40)}`);
    });
    document.querySelectorAll('input, select').forEach(element => {
      if (!(element as HTMLInputElement).labels?.length && !element.getAttribute('aria-label')) issues.push('unlabelled form control');
    });
    return issues;
  });
}

for (const device of devices) {
  test(`end-to-end journey on ${device.name}`, async ({ browser }) => {
    const profile: DeviceProfile = device;
    const context = await browser.newContext({
      viewport: profile.viewport,
      deviceScaleFactor: profile.deviceScaleFactor,
      isMobile: profile.isMobile,
      hasTouch: profile.hasTouch,
    });
    const page = await context.newPage();
    try {
      await mockModelHealth(page);
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      const midi = new midiPackage.Midi();
      const guitar = midi.addTrack();
      guitar.name = 'Lead guitar'; guitar.instrument.number = 24;
      guitar.addNote({ midi: 60, time: 0, duration: 0.5 });
      guitar.addNote({ midi: 64, time: 9, duration: 0.5 });
      const job = { id: 'journey-song', title: 'Journey song', youtube_url: 'https://youtu.be/example',
        status: 'done', config: { transcription: { model: 'medium' } }, progress: {},
        audio_key: 'source.m4a', midi_key: 'out.mid', events_key: 'events.json', retry_count: 0 };
      await page.route('**/api/jobs?*', route => route.fulfill({ json: [job] }));
      await page.route('**/api/jobs/journey-song/midi', route => route.fulfill({
        contentType: 'audio/midi', body: Buffer.from(midi.toArray()) }));
      await page.goto('/');

      // Jobs: queue form, card, filter, artifacts.
      await expect(page.getByRole('heading', { name: 'Queue a song' })).toBeVisible();
      await expect(page.getByTestId('job-card')).toHaveCount(1);
      await page.getByLabel('Show', { exact: true }).selectOption('done');
      await expect(page.getByTestId('job-card')).toHaveCount(1);
      await page.getByLabel('Show', { exact: true }).selectOption('failed');
      await expect(page.getByText('No matching jobs', { exact: true })).toBeVisible();
      await page.getByLabel('Show', { exact: true }).selectOption('all');
      await expect(page.getByRole('heading', { name: 'Transcriptions (1)' })).toBeVisible();

      // Preview playback: play, pause, seek, resume.
      await page.getByTestId('job-card').getByRole('button', { name: 'Preview MIDI' }).click();
      await expect(page.getByRole('heading', { name: 'MIDI preview' })).toBeVisible();
      const player = page.locator('.player');
      await player.getByRole('button', { name: 'Play', exact: true }).click();
      const slider = player.getByRole('slider');
      await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0.2);
      await player.getByRole('button', { name: 'Pause', exact: true }).click();
      await slider.fill('5');
      await expect(slider).toHaveValue('5');
      expect(await auditLayout(page)).toEqual([]);

      // Visualization: synchronized transport, tabs seeking, roll playhead.
      await page.getByRole('tab', { name: 'Visualization', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'MIDI visualization' })).toBeVisible();
      await expect.poll(() => page.getByTestId('alphatab-score').locator('svg').count()).toBeGreaterThan(0);
      const transport = page.getByLabel('Visualization playback position');
      await expect(transport).toBeVisible();
      await page.getByRole('button', { name: 'Measure 2', exact: true }).click();
      await expect(transport).not.toHaveValue('0');
      await page.getByRole('button', { name: 'Piano roll', exact: true }).click();
      await expect(page.getByTestId('roll-playhead')).toHaveCount(1);
      await page.getByRole('button', { name: 'Play', exact: true }).click();
      await expect.poll(async () => Number(await transport.inputValue())).toBeGreaterThan(2.1);
      await expect(page.getByTestId('roll-playhead')).toHaveCount(1);
      await page.getByRole('button', { name: 'Pause', exact: true }).click();
      expect(await auditLayout(page)).toEqual([]);
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });
}
