# Workspace voice engine

This process consumes the private Site's queue and runs the already-installed
Chatterbox Turbo model. It is not a hosted inference API. Jobs wait when this
workspace is suspended or the engine is stopped.

From the repository root, use the environment installed by `voice-tools/setup_cpu.sh`:

```bash
voice-tools/.venv/bin/python voice-studio/engine/worker.py
```

For a single queued job, add `--once`. A filesystem lock prevents a second
engine from using the same CPU and memory. A stable worker ID is kept under
`voice-tools/site-jobs/worker.id`. Set `VOICE_LAB_ROOT` to use a different model
and job-data directory. This exported copy does not start or replace the live
workspace engine.

The owner prepares `engine/.runtime.json` with these keys:

```json
{
  "site_url": "https://YOUR-SITE.chatgpt.site",
  "site_service_token": "SET_PRIVATELY",
  "engine_token": "SET_PRIVATELY"
}
```

Keep that file mode 0600 and excluded from Git. Both credentials are transmitted
only to that configured HTTPS Site origin. Redirects are rejected. No credential
or reference audio is printed to logs.

The engine sends a heartbeat every 15 seconds and processes one narration at a
time. It loads Turbo once for each job, conditions the new reference once, and
saves each text section atomically. The manifest records input hashes, exact
script sections, generation settings, deterministic seeds and WAV hashes. A
failed or cancelled job can resume matching, verified sections. Successful jobs
remove local audio after both final files have been uploaded and completion has
been acknowledged; the private Site retains its own files.

Balanced delivery uses temperature 0.70, top-p 0.90, repetition penalty 1.2 and
seed 42 plus section index. Expressive delivery uses 0.85, 0.95 and seed 123 plus
section index. Both use the approved Turbo model with `nano=False`; upstream
audio watermarking remains enabled.

References must be longer than five and no longer than thirty seconds, at most
20 MB, in WAV/MP3/M4A/MP4/AAC/FLAC/OGG/WebM format. Scripts are limited to 6,000 words and 40,000
characters. Generation preserves all non-whitespace text and uses sections of
at most 250 characters. Output is mono 24 kHz PCM16 WAV and 128 kbps MP3.
The Site accepts final files up to 100 MB each; exceptionally long narrations
may need separate jobs.

Keep only one heavy inference process active. The existing workspace has two
CPU cores and 8 GiB RAM. The engine checks free disk before starting a job and
does not download model weights during generation. Voice likeness and human-like
delivery still need listening; waveform integrity checks do not establish either.

The loop retries temporary network failures without discarding checkpoints.
Cancellation is checked between sections, while uploads are prepared, and on
heartbeat responses. An in-flight section must finish before it is stopped.
