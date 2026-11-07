import { test, expect } from '@playwright/test';
import midiPackage from '@tonejs/midi';
import { mockModelHealth } from './health-fixture';

test.beforeEach(async ({ page }) => mockModelHealth(page));

test('artifact retention and MIDI playback, pause, seeking and replay', async ({ page }) => {
  const failures: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  const midi = new midiPackage.Midi();
  midi.addTrack().addNote({ midi: 60, time: 0, duration: 10 });
  const job = { id: 'done-song', title: 'Test song', youtube_url: 'https://youtu.be/example',
    status: 'done', config: { transcription: { model: 'medium' } }, progress: {}, audio_key: 'source.m4a', midi_key: 'out.mid', events_key: 'events.json' };
  await page.route('**/api/jobs?*', route => route.fulfill({ json: [job,
    { ...job, id: 'cancelled-song', status: 'cancelled', midi_key: null }] }));
  await page.route('**/api/jobs/done-song/midi', route => route.fulfill({
    contentType: 'audio/midi', body: Buffer.from(midi.toArray()) }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Downloaded artifacts (2)' })).toBeVisible();
  const player = page.locator('.player');
  await player.getByRole('button', { name: 'Play', exact: true }).click();
  const slider = player.getByRole('slider');
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(0.2);
  await player.getByRole('button', { name: 'Pause', exact: true }).click();
  await slider.fill('5');
  await expect(slider).toHaveValue('5');
  await page.waitForTimeout(150);
  await expect(slider).toHaveValue('5');
  await player.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(5.1);
  await slider.fill('10');
  await expect(player.getByRole('button', { name: 'Play', exact: true })).toBeVisible();
  await player.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(async () => Number(await slider.inputValue())).toBeLessThan(1);
  expect(failures).toEqual([]);
});
