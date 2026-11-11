# Restore and reuse this project

## What is saved

`voice-studio/` contains the interface, hosted API, private queue protocol,
resumable generation worker, build helper, tests and configuration templates.
`voice-tools/` contains the approved model settings, standalone generation CLI,
model downloaders, transcript checker and current dependency snapshot.

The production source was copied from commit
`795228905777722a8c33c516635a08f84ff45818` in the private Sites source repository.
Only the exported copy received portability adjustments. It uses a configurable
model directory, a Turbo downloader that works without Nano, and a standalone
CLI defaulting to the approved Turbo settings. The original sample audio files
remain in this repository for comparison.

## Reproduce the voice generation

1. Clone this repository on a Linux x86-64 machine.
2. Install Python 3.12 with venv support, ffmpeg and Git.
3. Run `bash voice-tools/setup_cpu.sh` once. It downloads the public model files
   and Python dependencies; subsequent synthesis uses the saved local model.
4. Run the command in `voice-tools/README.md` with a new reference and script.

Keep model/data storage on a persistent disk. You do not need to download or
train the voice model again for every script. Each new recording supplies the
voice reference; text supplies the words.

## Approved settings

| Preset | Temperature | top_p | repetition_penalty | Base seed |
| --- | --- | --- | --- | --- |
| Balanced / take A | 0.70 | 0.90 | 1.2 | 42 |
| Expressive / take B | 0.85 | 0.95 | 1.2 | 123 |

Model: Chatterbox Turbo, `nano=False`. The long-form worker uses at most 250
characters per section, deterministic per-section seeds, saved checkpoints,
and ordered assembly into WAV and MP3. It retains the upstream watermark.

## Reproduce the existing Sites application

The hosted Worker cannot run as an ordinary Python web server. To use it on
Sites, register a separate private Site, configure the R2 binding `BUCKET`,
set the hosted secret `ENGINE_TOKEN`, and create the local ignored runtime
configuration with matching credentials. Obtain service access through the
platform's supported private-site workflow, never by publishing secrets in Git.

Copy `voice-studio/.openai/hosting.example.json` to
`voice-studio/.openai/hosting.json` and replace the placeholder with the returned
Site ID. From `voice-studio/`:

```bash
node --test tests/worker.test.mjs
node scripts/build.mjs
```

The deploy archive must contain `.openai/hosting.json` and
`dist/server/index.js` at those paths. Publish through the supported Sites
workflow. Then start the workspace engine with its configured interpreter.
The engine and hosted app are separate processes: uploading files does not
start a stopped computer automatically.

## What is deliberately not in GitHub

- Tokens, service credentials, real runtime configuration or `.env` files.
- Users' reference recordings, private scripts and saved generation jobs.
- Virtual environments, caches and multi-gigabyte model weights.
- The active deployment's actual project identity.

Recreate configuration privately and run the download/setup scripts. This is a
source backup, not a full backup of users' stored audio or cloud resources.

## Validation scope

The existing live Site previously passed a real two-section generation and
MP3/WAV download check. The export is checked with API tests, worker tests,
Python/JavaScript syntax checks and a build using a temporary placeholder
manifest. A fresh AWS instance and a fresh dependency installation are not
tested or provisioned by this backup. See the AWS guide for future work.
