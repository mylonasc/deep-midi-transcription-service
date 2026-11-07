import { test, expect } from '@playwright/test';
import midiPackage from '@tonejs/midi';
import * as alphaTab from '@coderline/alphatab';
import { greedyFretResolver, GUITAR_TUNING } from '../src/visualization/fretting';
import { buildAlphaTex } from '../src/visualization/alphaTex';
import type { MidiSong, SongNote } from '../src/visualization/types';
import { mockModelHealth } from './health-fixture';

const note = (id: string, pitch: number, startTick = 0): SongNote => ({
  id, pitch, startTick, durationTicks: 480, time: 0, duration: 0.5, velocity: 0.8,
});

test('fret resolver preserves pitch and prevents overlapping string assignments', () => {
  const input = [note('a', 60), note('b', 64), note('c', 60, 120), note('outside', 110)];
  const original = JSON.stringify(input);
  const result = greedyFretResolver.resolve(input, GUITAR_TUNING, 24);
  expect(result.omitted).toBe(1);
  expect(JSON.stringify(input)).toBe(original);
  for (const n of result.notes) {
    expect(GUITAR_TUNING[n.string - 1] + n.fret).toBe(n.pitch);
    for (const other of result.notes) {
      if (other.id === n.id || other.string !== n.string) continue;
      expect(n.startTick + n.durationTicks <= other.startTick || other.startTick + other.durationTicks <= n.startTick).toBe(true);
    }
  }
});

test('alphaTab adapter produces valid scores independently of fret assignment', () => {
  const song: MidiSong = { ppq: 480, tempo: 120, duration: 1, durationTicks: 960, tracks: [{
    id: '0', name: 'Untrusted " title \\ ts', instrument: 'guitar', program: 24, percussion: false,
    notes: [note('a', 60), note('b', 64)],
  }] };
  for (const mode of ['score', 'guitar', 'bass'] as const) {
    const result = buildAlphaTex(song, song.tracks[0], mode, 0);
    const importer = new alphaTab.importer.AlphaTexImporter();
    importer.initFromString(result.tex, new alphaTab.Settings());
    const score = importer.readScore();
    expect(score.masterBars).toHaveLength(4);
    expect(score.tracks[0].staves[0].bars[0].voices[0].beats).toHaveLength(16);
  }
});

for (const width of [375, 1280]) {
  test(`visualization renders alphaTab and independent piano roll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockModelHealth(page);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const midi = new midiPackage.Midi();
    const guitar = midi.addTrack();
    guitar.name = 'Lead guitar'; guitar.instrument.number = 24;
    guitar.addNote({ midi: 60, time: 0, duration: 0.5 });
    guitar.addNote({ midi: 64, time: 0, duration: 1 });
    guitar.addNote({ midi: 105, time: 1, duration: 0.2 });
    guitar.addNote({ midi: 62, time: 25, duration: 0.5 });
    const piano = midi.addTrack(); piano.name = 'Low piano'; piano.channel = 1;
    piano.addNote({ midi: 30, time: 0, duration: 1 });
    const drums = midi.addTrack(); drums.name = 'Drums'; drums.channel = 9;
    drums.addNote({ midi: 36, time: 0.2, duration: 0.1 });
    await page.route('**/api/jobs?*', route => route.fulfill({ json: [{ id: 'visual-song', title: 'Prototype song',
      status: 'done', config: { transcription: { model: 'medium' } }, progress: {}, midi_key: 'out.mid' }] }));
    await page.route('**/api/jobs/visual-song/midi', route => route.fulfill({ contentType: 'audio/midi', body: Buffer.from(midi.toArray()) }));
    await page.goto('/');
    await page.getByRole('tab', { name: 'Visualization', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'MIDI visualization' })).toBeVisible();
    await expect.poll(() => page.getByTestId('alphatab-score').locator('svg').count()).toBeGreaterThan(0);
    await expect(page.getByText('1 notes across this track', { exact: false })).toBeVisible();
    await page.getByLabel('Notation format').selectOption('score');
    await expect.poll(() => page.getByTestId('alphatab-score').locator('svg').count()).toBeGreaterThan(0);
    await page.getByLabel('Instrument track').selectOption({ label: 'Drums · standard kit (drums)' });
    await expect(page.getByText('Percussion notation is not supported yet.', { exact: false })).toBeVisible();
    await page.getByRole('button', { name: 'Piano roll', exact: true }).click();
    await expect(page.getByLabel('Piano-roll tracks')).toHaveValue('all');
    await expect(page.getByTestId('roll-note')).toHaveCount(5); // Includes omitted high pitch, piano and drums.
    await page.getByLabel('Piano-roll tracks').selectOption('2');
    await expect(page.getByTestId('roll-note')).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole('tab', { name: 'Jobs & artifacts' }).click();
    await expect(page.getByRole('heading', { name: 'Queue a song' })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
