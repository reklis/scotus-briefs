# Private transcription

The transcription worker obtains a five-minute S3 download URL and writes audio only inside an ephemeral temporary directory. It verifies byte count and SHA-256 before decoding, converts non-PCM inputs to mono 16 kHz WAV through `ffmpeg`, and removes the temporary directory after each call.

The default adapter uses `faster-whisper` and is installed with `uv sync --extra stt`. Every revision records model/configuration hash and DCFD hint-set version. Silent or unintelligible audio is recorded as `non_transcribable`; the service never asks a model to fill silence.

Hints in `resources/dcfd-hints-v1.yaml` provide unit, talkgroup, street, quadrant, and landmark vocabulary. They are context, not evidence. A model/configuration change creates a new immutable revision and leaves prior provenance intact.
