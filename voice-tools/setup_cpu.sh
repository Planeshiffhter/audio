#!/usr/bin/env bash
set -euo pipefail
VOICE_TOOLS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VOICE_PYTHON="${VOICE_PYTHON:-python3.12}"
command -v "$VOICE_PYTHON" >/dev/null
command -v ffmpeg >/dev/null || { echo 'Install ffmpeg before running this script.' >&2; exit 1; }
command -v git >/dev/null || { echo 'Install git before running this script.' >&2; exit 1; }
"$VOICE_PYTHON" -m venv "$VOICE_TOOLS_DIR/.venv"
"$VOICE_TOOLS_DIR/.venv/bin/python" -m pip install --upgrade pip
"$VOICE_TOOLS_DIR/.venv/bin/python" -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.6.0+cpu' 'torchaudio==2.6.0+cpu'
"$VOICE_TOOLS_DIR/.venv/bin/python" -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r "$VOICE_TOOLS_DIR/requirements.lock.txt"
"$VOICE_TOOLS_DIR/.venv/bin/python" "$VOICE_TOOLS_DIR/download_turbo.py"
printf '%s\n' 'CPU environment and Turbo model are ready.'
