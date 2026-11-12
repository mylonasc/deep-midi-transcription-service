// Production import gate: no partial/mis-timed notation is published as success.
const assert = require('node:assert/strict');
const alphaTab = require('@coderline/alphatab');
function order(a, b) { return a[0] - b[0] || a[2] - b[2] || a[1] - b[1]; }
function read(tex) {
  const importer = new alphaTab.importer.AlphaTexImporter();
  importer.initFromString(tex, new alphaTab.Settings());
  const score = importer.readScore();
  const notes = [];
  for (const track of score.tracks) for (const staff of track.staves) for (const bar of staff.bars)
    for (const voice of bar.voices) for (const beat of voice.beats) for (const note of beat.notes) {
      if (note.isTieDestination) continue;
      let end = beat.absolutePlaybackStart + beat.playbackDuration;
      let tied = note.tieDestination;
      while (tied) { end = tied.beat.absolutePlaybackStart + tied.beat.playbackDuration; tied = tied.tieDestination; }
      notes.push([beat.absolutePlaybackStart / 2, end / 2, note.realValue]);
    }
  return { score, notes: notes.sort(order) };
}
function verify(document, tex) {
  const expected = document.source_notes.map(n => [n.start_tick, n.end_tick, n.pitch]).sort(order);
  const full = read(tex);
  assert.deepEqual(full.notes, expected, 'AlphaTex must preserve every pitch/attack/release');
  // Master-bar automations include beat effects after score finalization.
  const tempi = new Map();
  for (const bar of full.score.masterBars) for (const event of bar.tempoAutomations)
    tempi.set(bar.start / 2 + event.ratioPosition * bar.calculateDuration() / 2, event.value);
  const end = Math.max(...document.source_notes.map(n => n.end_tick));
  for (const event of document.tempo_map.filter(e => e.tick < end)) {
    const actual = [...tempi].find(([tick]) => Math.abs(tick - event.tick) < 0.000001)?.[1];
    assert.ok(actual !== undefined && Math.abs(actual - 60000000 / event.microseconds_per_beat) < 0.000001,
      `Tempo mismatch at tick ${event.tick}: ${actual}`);
  }
  for (const event of document.meter_map.filter(e => e.tick < end)) {
    const bar = full.score.masterBars.find(b => b.start / 2 === event.tick);
    assert.ok(bar && bar.timeSignatureNumerator === event.numerator && bar.timeSignatureDenominator === event.denominator,
      `Meter mismatch at tick ${event.tick}`);
  }
  for (const page of document.notation_pages) {
    const clipped = expected.filter(n => n[0] < page.end_tick && n[1] > page.start_tick)
      .map(n => [Math.max(n[0], page.start_tick) - page.start_tick, Math.min(n[1], page.end_tick) - page.start_tick, n[2]])
      .sort(order);
    assert.deepEqual(read(page.tex).notes, clipped, `Excerpt ${page.index} must preserve clipped intervals`);
  }
}
module.exports = { verify };
if (require.main === module) {
  const fs = require('node:fs');
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  verify(input.document, input.tex);
}
