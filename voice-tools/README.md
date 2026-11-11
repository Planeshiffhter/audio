# Reusable voice tools

The approved voice model is **Chatterbox Turbo**. The exported CLI defaults to
Turbo with Balanced settings; it can also select Expressive or legacy Nano.
The live ChatGPT workspace and its scripts were not changed by this export.

## Install once on a new Linux machine

Install Python 3.12 with its venv support, Git and ffmpeg using the machine's
package manager. Use an x86-64 host with at least 16 GB RAM for useful headroom,
and enough disk for Python packages, approximately 3 GB of Turbo model files,
uploads and generated audio. The earlier 8 GB workspace worked but was tight.

From the repository root:

```bash
bash voice-tools/setup_cpu.sh
```

This installs CPU-only PyTorch, the captured dependencies and the pinned public
Turbo checkpoint. It needs internet access during setup. Later synthesis uses
local weights and offline mode. No paid speech API or API key is required.
Cloud compute/storage still cost money when you rent a server.

`requirements.lock.txt` is a snapshot of the working Python 3.12 Linux
environment, including the later ASR packages. It is not a lock for every OS or
GPU driver. The fresh installation script has been syntax-checked; a complete
fresh-machine installation was not run as part of this source backup.

## Generate a narration

```bash
voice-tools/.venv/bin/python voice-tools/voice.py \
  --reference /path/to/my-voice.wav \
  --script /path/to/my-script.txt \
  --preset balanced \
  --output /path/to/narration.wav
```

Use a clear voice sample longer than five seconds; around ten seconds works.
WAV, MP3, M4A and other ffmpeg-readable input formats are accepted by the CLI.
Use `--preset expressive` for the other approved delivery setting, or `--text`
instead of `--script` for a short test. The script's words are preserved and
long text is split into sections. The CLI writes its final output atomically,
but does not checkpoint completed sections between invocations. The website
engine does checkpoint sections for resumable long jobs.

Convert to MP3:

```bash
ffmpeg -i narration.wav -codec:a libmp3lame -b:a 128k narration.mp3
```

The upstream audio watermark remains enabled. Voice similarity and delivery
depend on the sample and the text; neither mode guarantees an exact match.

## Other included tools

- `download_turbo.py`: preferred pinned model downloader; works without Nano.
- `download_model.py`: optional legacy Nano downloader.
- `improve_voice.py`: original short two-take experiment, with hardcoded demo
  text/reference. Use `voice.py` for new inputs, rather than running this demo.
- `check_transcript.py`: independent CPU speech-recognition check. It downloads
  public base.en weights on first use and preserves the ffmpeg decoding fix.
- `../voice-studio/engine/worker.py`: resumable website queue consumer. Its
  default model root is this folder; set `VOICE_LAB_ROOT` to override it.

The worker still speaks the current Sites API. See the AWS guide before trying
to run the complete web application on AWS.
