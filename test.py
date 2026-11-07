from pathlib import Path
from muscriptor import TranscriptionModel

# "medium" resolves to hf://MuScriptor/muscriptor-medium and downloads on first use.
model = TranscriptionModel.load_model("medium")

# Get a MIDI file directly:
Path("out.mid").write_bytes(model.transcribe_and_postprocess("arnakia.mp3")[0])

# Or stream note events as they are transcribed:
for event in model.transcribe("arnakia.mp3"):
    print(event)  # NoteStartEvent / NoteEndEvent / ProgressEvent

