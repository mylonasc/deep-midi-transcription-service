"""Download-free inspection and canonical tablature boundary tests."""
import importlib
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch

import mido
from fastapi.testclient import TestClient
from pydantic import ValidationError

from transc_service.tablature.midi import MidiInspectionError, inspect_midi
from transc_service.tablature.models import TablatureOptions, TablatureResult


def midi_bytes(*tracks, ppq=480, midi_type=1):
    midi = mido.MidiFile(type=midi_type, ticks_per_beat=ppq)
    midi.tracks.extend(mido.MidiTrack(track) for track in tracks)
    buffer = BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def guitar_track():
    return [mido.MetaMessage("track_name", name="Guitar"),
            mido.Message("program_change", program=24),
            mido.Message("note_on", note=64, velocity=80, time=120),
            mido.Message("note_off", note=64, time=480)]


class MidiInspectionTests(unittest.TestCase):
    def test_defaults_and_stable_note_identity(self):
        data = midi_bytes(guitar_track())
        result = inspect_midi(data)
        summary = result.inspection
        self.assertEqual(summary, inspect_midi(data).inspection)
        self.assertEqual(summary.source_ppq, 480)
        self.assertEqual(summary.tempo_map[0].microseconds_per_beat, 500000)
        self.assertEqual(summary.meter_map[0].numerator, 4)
        part = summary.parts[0]
        self.assertEqual((part.id, part.track, part.channel, part.program), ("t0:c0:p24", 0, 0, 24))
        self.assertTrue(part.guitar_program)
        self.assertEqual(part.name, "Guitar")
        note = result.notes_by_part[part.id][0]
        self.assertEqual((note.id, note.start_tick, note.end_tick), ("t0:c0:n0", 120, 600))
        self.assertEqual((note.start_seconds, note.end_seconds), (0.125, 0.625))
        self.assertEqual(note.velocity, 80)
        self.assertFalse(summary.diagnostics)
        self.assertFalse(part.diagnostics)

    def test_grouping_uses_track_channel_and_attack_program(self):
        track = [mido.Message("program_change", program=24),
                 mido.Message("note_on", note=64),
                 mido.Message("program_change", program=25, time=120),
                 mido.Message("note_on", note=67),
                 mido.Message("note_on", channel=1, note=60),
                 mido.Message("note_on", channel=9, note=36),
                 mido.Message("note_off", note=64, time=120),
                 mido.Message("note_off", note=67),
                 mido.Message("note_off", channel=1, note=60)]
        # A different track with the same program/channel remains a separate part.
        result = inspect_midi(midi_bytes(track, guitar_track()))
        self.assertEqual([part.id for part in result.inspection.parts],
                         ["t0:c0:p24", "t0:c0:p25", "t0:c1:p0", "t1:c0:p24"])
        self.assertEqual(result.inspection.ignored_drum_attacks, 1)
        self.assertEqual(result.notes_by_part["t0:c0:p24"][0].end_tick, 240)
        self.assertEqual(result.inspection.parts[2].diagnostics[0].code, "non_guitar_program")

    def test_equal_pitch_releases_are_fifo_and_zero_velocity_is_release(self):
        result = inspect_midi(midi_bytes([
            mido.Message("note_on", note=64),
            mido.Message("note_on", note=64, time=120),
            mido.Message("note_on", note=64, velocity=0, time=120),
            mido.Message("note_off", note=64, time=120),
        ]))
        self.assertEqual([(note.start_tick, note.end_tick) for note in result.notes_by_part["t0:c0:p0"]],
                         [(0, 240), (120, 360)])

    def test_tempo_map_preserves_initial_default_and_exact_original_seconds(self):
        conductor = [mido.MetaMessage("set_tempo", tempo=1000000, time=480)]
        result = inspect_midi(midi_bytes(conductor, [
            mido.Message("note_on", note=64, time=240),
            mido.Message("note_off", note=64, time=480),
        ]))
        self.assertEqual([event.time_seconds for event in result.inspection.tempo_map], [0, 0.5])
        note = result.notes_by_part["t1:c0:p0"][0]
        self.assertEqual((note.start_seconds, note.end_seconds), (0.25, 1.0))
        self.assertIn("variable_tempo", [item.code for item in result.inspection.diagnostics])
        constant = inspect_midi(midi_bytes([
            mido.MetaMessage("set_tempo", tempo=1000000), *guitar_track(),
        ]))
        self.assertEqual(constant.inspection.tempo_map[0].microseconds_per_beat, 1000000)
        self.assertNotIn("variable_tempo", [item.code for item in constant.inspection.diagnostics])

    def test_meter_changes_are_retained_for_export(self):
        result = inspect_midi(midi_bytes([
            mido.MetaMessage("time_signature", numerator=3, denominator=4, time=480),
            *guitar_track(),
        ]))
        self.assertEqual([(item.source_tick, item.numerator) for item in result.inspection.meter_map],
                         [(0, 4), (480, 3)])
        warning = next(item for item in result.inspection.diagnostics if item.code == "meter_changes")
        self.assertFalse(warning.blocks_generation)

    def test_fractional_tick_rounding_retains_source_and_reports_collapsed_notes(self):
        result = inspect_midi(midi_bytes([
            mido.Message("note_on", note=64, time=1),
            mido.Message("note_off", note=64, time=1),
        ], ppq=1920))
        note = result.notes_by_part["t0:c0:p0"][0]
        self.assertEqual((note.source_start_tick, note.source_end_tick), (1, 2))
        self.assertEqual((note.start_tick, note.end_tick), (0, 0))
        self.assertEqual(note.end_seconds, 2 * 0.5 / 1920)
        self.assertEqual(result.inspection.rounded_tick_values, 2)
        self.assertIn("collapsed_duration", [item.code for item in result.inspection.parts[0].diagnostics])
        self.assertIn("tick_rounding", [item.code for item in result.inspection.diagnostics])

    def test_integer_tick_scaling(self):
        result = inspect_midi(midi_bytes(guitar_track(), ppq=960))
        note = result.notes_by_part["t0:c0:p24"][0]
        self.assertEqual((note.start_tick, note.end_tick), (60, 300))
        self.assertEqual(result.inspection.rounded_tick_values, 0)

    def test_features_are_scoped_to_track_and_channel(self):
        track = [mido.Message("control_change", control=64, value=127),
                 mido.Message("pitchwheel", pitch=500), *guitar_track()]
        result = inspect_midi(midi_bytes(track, guitar_track()))
        self.assertEqual({item.code for item in result.inspection.parts[0].diagnostics},
                         {"sustain_pedal", "pitch_bend"})
        self.assertFalse(result.inspection.parts[1].diagnostics)
        self.assertTrue(all(item.blocks_generation for item in result.inspection.parts[0].diagnostics))

    def test_pitch_range_and_no_pitched_notes(self):
        result = inspect_midi(midi_bytes([
            mido.Message("note_on", note=30), mido.Message("note_off", note=30, time=480),
        ]))
        self.assertIn("pitch_out_of_range", [item.code for item in result.inspection.parts[0].diagnostics])
        empty = inspect_midi(midi_bytes([mido.Message("note_on", channel=9, note=36)]))
        self.assertFalse(empty.inspection.parts)
        self.assertIn("no_pitched_notes", [item.code for item in empty.inspection.diagnostics])

    def test_malformed_and_structurally_unsupported_files(self):
        cases = [b"not midi", b"MThd", midi_bytes(guitar_track(), midi_type=2),
                 midi_bytes(guitar_track(), ppq=-24),
                 midi_bytes([mido.Message("note_off", note=64)]),
                 midi_bytes([mido.Message("note_on", note=64)]),
                 midi_bytes([mido.Message("note_on", note=64), mido.Message("note_off", note=64)]),
                 midi_bytes([mido.MetaMessage("set_tempo", tempo=0)]),
                 midi_bytes([mido.MetaMessage("set_tempo", tempo=500000)],
                            [mido.MetaMessage("set_tempo", tempo=1000000)])]
        for data in cases:
            with self.subTest(data=data[:30]), self.assertRaises(MidiInspectionError):
                inspect_midi(data)

    def test_inspection_limits(self):
        with patch("transc_service.tablature.midi.MAX_MIDI_BYTES", 4), self.assertRaises(MidiInspectionError):
            inspect_midi(midi_bytes(guitar_track()))
        with patch("transc_service.tablature.midi.MAX_MIDI_MESSAGES", 2), self.assertRaises(MidiInspectionError):
            inspect_midi(midi_bytes(guitar_track()))


class TablatureContractTests(unittest.TestCase):
    def result(self):
        inspected = inspect_midi(midi_bytes(guitar_track()))
        summary = inspected.inspection
        return {
            "options": {"part_id": "t0:c0:p24", "source_midi_sha256": summary.source_midi_sha256},
            "provenance": {"source_revision": "a" * 40, "model_id": "mylonasc/open-fret-tab",
                           "model_revision": "b" * 40},
            "source_ppq": summary.source_ppq,
            "tempo_map": summary.tempo_map, "meter_map": summary.meter_map,
            "source_notes": inspected.notes_by_part["t0:c0:p24"],
            "assignments": [{"source_note_id": "t0:c0:n0", "string": 1, "fret": 0}],
        }

    def test_round_trip(self):
        result = TablatureResult.model_validate(self.result())
        self.assertEqual(result, TablatureResult.model_validate_json(result.model_dump_json()))

    def test_options_reject_unsupported_configuration(self):
        base = self.result()["options"]
        for update in [{"capo": 1}, {"model_path": "/unsafe"}, {"decoding": "legacy"},
                       {"max_fret": 25}, {"part_id": "0"}, {"source_midi_sha256": "bad"},
                       {"tuning": [64, 59, 55, 50, 45, 38]}]:
            with self.subTest(update=update), self.assertRaises(ValidationError):
                TablatureOptions.model_validate({**base, **update})

    def test_invalid_assignment_fails(self):
        for assignment in [[], [{"source_note_id": "missing", "string": 1, "fret": 0}],
                           [{"source_note_id": "t0:c0:n0", "string": 1, "fret": 1}],
                           [{"source_note_id": "t0:c0:n0", "string": 7, "fret": 0}]]:
            with self.subTest(assignment=assignment), self.assertRaises(ValidationError):
                TablatureResult.model_validate({**self.result(), "assignments": assignment})

    def test_duplicate_and_sustained_string_conflicts_fail(self):
        body = self.result()
        original = body["source_notes"][0]
        body["source_notes"].append(original.model_copy(update={"id": "second"}))
        body["assignments"].append({"source_note_id": "second", "string": 1, "fret": 0})
        with self.assertRaisesRegex(ValidationError, "overlapping"):
            TablatureResult.model_validate(body)
        body["assignments"][1]["source_note_id"] = original.id
        with self.assertRaisesRegex(ValidationError, "exactly one"):
            TablatureResult.model_validate(body)
        body["source_notes"][1] = original
        with self.assertRaisesRegex(ValidationError, "unique"):
            TablatureResult.model_validate(body)

    def test_release_boundary_allows_same_string_reuse(self):
        body = self.result()
        body["source_notes"].append(body["source_notes"][0].model_copy(update={
            "id": "second", "source_start_tick": 600, "source_end_tick": 1080,
            "start_tick": 600, "end_tick": 1080, "start_seconds": 0.625, "end_seconds": 1.125}))
        body["assignments"].append({"source_note_id": "second", "string": 1, "fret": 0})
        self.assertEqual(len(TablatureResult.model_validate(body).assignments), 2)


class MidiPartsApiTests(unittest.TestCase):
    def test_inspect_existing_artifact_without_changing_job(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DATA_DIR": directory}):
            module = importlib.import_module("transc_service.app")
            from transc_service.models import JobStatus
            from transc_service.config import ServerConfig
            from transc_service.pipeline.manager import JobManager
            from transc_service.stores.local import LocalObjectStore
            from transc_service.stores.sqlite import SqliteMetadataStore
            meta = SqliteMetadataStore(directory + "/meta.db")
            objects = LocalObjectStore(directory + "/objects")
            manager = JobManager(meta, objects)

            async def start():
                await meta.init()

            with patch.object(manager, "start", start), patch.multiple(module,
                meta=meta, objects=objects, manager=manager, cfg=ServerConfig(data_dir=directory),
            ), TestClient(module.app) as client:
                self.assertEqual(client.get("/jobs/missing/midi-parts").status_code, 404)
                job_id = client.post("/jobs", json={"youtube_url": "https://youtu.be/x"}).json()["id"]
                url = f"/jobs/{job_id}/midi-parts"
                self.assertEqual(client.get(url).status_code, 404)

                async def publish(data):
                    job = await meta.get(job_id)
                    job.status = JobStatus.FAILED  # availability is not determined by status
                    job.midi_key = f"jobs/{job_id}/out.mid"
                    await objects.put_bytes(job.midi_key, data)
                    await meta.update(job)

                data = midi_bytes(guitar_track())
                client.portal.call(publish, data)
                before = client.get(f"/jobs/{job_id}").json()
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["parts"][0]["id"], "t0:c0:p24")
                self.assertNotIn("source_notes", response.json())
                self.assertEqual(client.get(f"/jobs/{job_id}").json(), before)
                self.assertEqual(client.get(f"/jobs/{job_id}/midi").content, data)
                client.portal.call(publish, b"broken")
                self.assertEqual(client.get(url).status_code, 422)
                client.portal.call(objects.delete_prefix, f"jobs/{job_id}")
                self.assertEqual(client.get(url).status_code, 404)
