# Audio

## Website source and reuse

This repository now includes the voice website and generation code, as well as
the audio samples below. The current ChatGPT-hosted site remains available at
[Rejoan Voice Studio](https://rejoan-voice-studio.kawahorejoan.chatgpt.site).
This backup does not migrate, replace, stop, or delete that site.

- [Website and queue engine source](voice-studio/)
- [Voice tools and installation instructions](voice-tools/)
- [Restore and reuse guide](docs/REUSE.md)
- [Future AWS migration guide](docs/AWS-MIGRATION.md)
- [Dependency and model sources](docs/THIRD-PARTY.md)

The current website backend uses ChatGPT Sites authentication and Cloudflare R2.
It is backed up here for reuse; it is **not yet a standalone AWS deployment**.
The AWS guide explains the remaining adapters. Secrets, uploaded voice samples,
private scripts, generated user jobs, caches and model weights are excluded.
The pinned model downloaders restore the weights without committing gigabytes
of binary files to GitHub.

## Revised narration — version 2

Two new AI-generated takes use the larger Chatterbox Turbo model and more conversational wording. Both use the supplied voice reference.

| Take | MP3 | WAV | Duration |
| --- | --- | --- | --- |
| A — balanced delivery settings | [Listen / download](https://raw.githubusercontent.com/Planeshiffhter/audio/main/render-quality-natural-v2.mp3) | [Download](https://raw.githubusercontent.com/Planeshiffhter/audio/main/render-quality-natural-v2.wav) | 10.42 seconds |
| B — more varied delivery settings | [Listen / download](https://raw.githubusercontent.com/Planeshiffhter/audio/main/render-quality-expressive-v2.mp3) | [Download](https://raw.githubusercontent.com/Planeshiffhter/audio/main/render-quality-expressive-v2.wav) | 10.66 seconds |

Start with take A, then compare take B. Both passed an independent speech-to-text check for the complete revised script.

### Revised script

> Here's the key point. Higher render quality alone won't make a basic model look cinematic. Character design, textures, lighting, and animation all need to work together.

The WAV files are 24 kHz, mono, 16-bit PCM. Version 2 MP3 files are encoded at 128 kbps. The model's audio watermark is retained.

## Original narration — version 1

AI-generated English narration made with Chatterbox Nano using the supplied voice reference. Duration: approximately 12 seconds.

- [Listen to or download the MP3](https://raw.githubusercontent.com/Planeshiffhter/audio/main/render-quality.mp3)
- [Download the WAV](https://raw.githubusercontent.com/Planeshiffhter/audio/main/render-quality.wav)

The WAV is 24 kHz, mono, 16-bit PCM. The MP3 is encoded at 96 kbps.

### Script

> The most important thing: Just increasing render quality won't automatically make a basic-looking model cinematic. Character design, texturing, lighting and animation quality all contribute.
