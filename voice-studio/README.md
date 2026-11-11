# Rejoan Voice Studio

## Exported source copy

This is a reusable copy of the functioning ChatGPT Sites app. Exporting it did
not change the live deployment or stop its voice engine. The original backend
still depends on Sites identity headers, private service access and Cloudflare
R2. An AWS deployment needs the adapters listed in
[the migration guide](../docs/AWS-MIGRATION.md).

The real Site ID and runtime credentials are intentionally excluded. To build
for a separately registered Sites project, copy
`.openai/hosting.example.json` to `.openai/hosting.json` and fill its project ID.
Do not deploy a clone to the existing live Site without explicitly intending to
update it. For the queue engine, copy `engine/runtime.example.json` to
`engine/.runtime.json`, configure credentials privately, and restrict file
permissions to the owner. The server's `ENGINE_TOKEN` must match that file.

The engine now defaults to the sibling `../voice-tools` directory. Override
`VOICE_LAB_ROOT` if your installed models are elsewhere. The private Git helper
also resolves this checkout's path instead of assuming the original workspace.
These portability adjustments apply only to this exported copy.

A private upload-and-generate studio using the existing Chatterbox Turbo model
in this workspace. Upload 5–30 seconds of reference speech, paste or import an
English script, choose Balanced or Expressive, and download MP3 or WAV.

## Architecture

- Hosted Cloudflare Worker: embedded responsive interface, owner-authorized API,
  private R2 storage, queue and job leases.
- Workspace engine: outbound authenticated polling, one CPU generation at a
  time, resumable validated audio sections, streaming assembly and export.
- The Site stays accessible when the workspace sleeps. Jobs wait until the
  workspace engine is running; this is not an always-on inference server.
- Private inputs and generated audio are stored in the Site's R2 bucket. No
  input recordings are committed to this source repository.

The two presets use the same Turbo settings as the user's approved samples.
New scripts are preserved; the app does not automatically rewrite them.

## Development

```bash
node --test tests/worker.test.mjs
/workspace/work/voice-lab/.venv/bin/python -m unittest discover -s engine -p 'test_*.py'
node scripts/build.mjs
```

The build embeds `frontend.html` into `dist/server/index.js`. There are no Node
runtime dependencies. `.openai/hosting.json` declares the private Site and R2
binding. The hosted `ENGINE_TOKEN` secret and ignored engine runtime file are
configured separately; never commit credentials.

See `engine/README.md` for the existing Python environment and queue consumer.
Model files are already stored under `/workspace/work/voice-lab/models/turbo`.

The managed browser tool is unavailable in this session, so UI validation uses
syntax and DOM-contract checks; API and engine checks are separate. Publishing
uses the exact committed source and built Worker artifact.
