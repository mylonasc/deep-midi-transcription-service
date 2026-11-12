import { test, expect } from '@playwright/test';
import midiPackage from '@tonejs/midi';
import { mockModelHealth } from './health-fixture';
import { devices } from './devices';
import * as alphaTab from '@coderline/alphatab';

test('fractional BPM tempo metadata preserves intra-measure timing without adding bars', () => {
  const importer = new alphaTab.importer.AlphaTexImporter();
  importer.initFromString('\\tempo 120\n\\track "Guitar"\n\\tuning (e4 b3 g3 d3 a2 e2)\n.\n\\tempo (117.70706634755 "" 0.0625) :4 0.1 r r r', new alphaTab.Settings());
  const score = importer.readScore();
  expect(score.masterBars).toHaveLength(1);
  const automation = score.masterBars[0].tempoAutomations.find(event => event.ratioPosition === 0.0625);
  expect(automation?.value).toBeCloseTo(60000000 / 509740, 9);
});

for (const device of [...devices, { ...devices[0], name: '320px compact', viewport: { width: 320, height: 700 } }]) {
  test(`generated tablature lifecycle and shared seeking on ${device.name}`, async ({ browser }) => {
    const context = await browser.newContext(device);
    const page = await context.newPage();
    try {
      await mockModelHealth(page);
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      const midi = new midiPackage.Midi();
      const track = midi.addTrack(); track.instrument.number = 24;
      track.addNote({ midi: 64, time: 0, duration: 1 });
      track.addNote({ midi: 59, time: 2, duration: 1 });
      const hash = 'a'.repeat(64);
      const options = { part_id: 't9:c1:p24', source_midi_sha256: hash };
      let task: any = null;
      let healthFailure = false;
      const job = { id: 'tabs-song', title: 'Tab source', status: 'done', config: { transcription: { model: 'medium' } },
        progress: {}, midi_key: 'out.mid', audio_key: null, events_key: null, retry_count: 0 };
      await page.route('**/api/jobs?*', route => route.fulfill({ json: [job] }));
      await page.route('**/api/jobs/tabs-song/midi', route => route.fulfill({ contentType: 'audio/midi', headers: { ETag: `"${hash}"` }, body: Buffer.from(midi.toArray()) }));
      await page.route('**/api/jobs/tabs-song/midi-parts', route => route.fulfill({ json: {
        source_midi_sha256: hash, diagnostics: [], ignored_drum_attacks: 1,
        parts: [{ id: options.part_id, name: 'Original backend part', program: 24, track: 9, channel: 1,
          note_count: 2, min_pitch: 59, max_pitch: 64, guitar_program: true, diagnostics: [] }],
      } }));
      await page.route('**/api/health/tablature', route => healthFailure
        ? route.fulfill({ status: 503, body: 'synthetic unavailable' })
        : route.fulfill({ json: { status: 'available', message: 'Synthetic pinned model' } }));
      await page.route('**/api/jobs/tabs-song/tablatures', async route => {
        if (route.request().method() === 'POST') {
          expect(route.request().postDataJSON()).toEqual(options); // Not frontend track index 0.
          task = { id: 'task-1', job_id: job.id, options, status: 'queued', phase: 'queued', completed: 0, total: 0,
            attempt: 0, error: null, artifacts: {} };
          await route.fulfill({ status: 202, json: task });
        } else await route.fulfill({ json: task ? [task] : [] });
      });
      await page.route('**/api/tablatures/task-1/retry', route => {
        task = { ...task, status: 'done', phase: 'complete', attempt: 1, error: null,
          artifacts: { json: 'x', alphatex: 'x', ascii: 'x', report: 'x' } };
        return route.fulfill({ status: 202, json: task });
      });
      const tex = '\\title "Stored fingering"\n\\tempo 120\n\\track "Guitar"\n\\tuning (e4 b3 g3 d3 a2 e2)\n.\n:4 0.1 0.2 r r';
      await page.route('**/api/tablatures/task-1/json', route => route.fulfill({ json: {
        options, limitations: ['Synthetic test fingerings'], notation_pages: [{ index: 0, tex,
          start_seconds: 0, end_seconds: 3, measure_seconds: [0, 1.5] }],
      } }));
      await page.route('**/api/tablatures/task-1', route => {
        task = null; return route.fulfill({ status: 204 });
      });
      await page.goto('/');
      await page.getByRole('tab', { name: 'Visualization', exact: true }).click();
      await page.getByRole('button', { name: 'Generated tablature', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Open-Fret generated tablature' })).toBeVisible();
      await page.getByLabel('Guitar tablature source part').selectOption(options.part_id);
      await page.getByRole('button', { name: 'Generate tablature', exact: true }).click();
      await expect(page.getByLabel('Tablature task / result')).toHaveValue('task-1');
      task = { ...task, status: 'failed', phase: 'inference', error: 'Diagnostic_' + 'x'.repeat(1000) };
      await expect(page.getByRole('button', { name: 'Retry tablature' })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      await page.getByRole('button', { name: 'Retry tablature' }).click();
      await expect(page.getByRole('link', { name: 'Download AlphaTex' })).toHaveAttribute('href', '/api/tablatures/task-1/alphatex');
      await expect.poll(() => page.getByTestId('alphatab-score').locator('svg').count()).toBeGreaterThan(0);
      await page.getByRole('button', { name: 'Seek tab measure 2', exact: true }).click();
      await expect(page.getByLabel('Visualization playback position')).toHaveValue('1.5');
      await expect(page.getByTestId('notation-playhead')).toBeVisible();
      const smallControls = await page.locator('button, select, .button-link').evaluateAll(elements => elements
        .filter(el => { const box = el.getBoundingClientRect(); return box.height > 0 && box.height < 44; }).map(el => el.textContent));
      expect(smallControls).toEqual([]);
      healthFailure = true;
      await expect(page.getByText('Tablature model: Availability unknown', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Generate tablature', exact: true })).toBeDisabled();
      await page.getByRole('button', { name: 'Delete tablature' }).click();
      await expect(page.getByLabel('Tablature task / result')).toHaveCount(0);
      await page.getByRole('button', { name: 'Piano roll', exact: true }).click();
      await expect(page.getByTestId('roll-note')).toHaveCount(2);
      expect(errors).toEqual([]);
    } finally { await context.close(); }
  });
}
