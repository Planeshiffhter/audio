#!/usr/bin/env python3
"""Transcribe local audio for script verification; never supply expected wording."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time
import subprocess


ROOT = Path(__file__).resolve().parent
MODEL_ID = "Systran/faster-whisper-base.en"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="+", type=Path, help="Local audio files to transcribe.")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "work" / "transcript-checks.json",
        help="JSON report path; default stays in voice-lab/work.",
    )
    parser.add_argument(
        "--model-dir", type=Path, default=ROOT / "models" / "whisper-base-en",
        help="Local model directory; public base.en weights are downloaded if missing.",
    )
    args = parser.parse_args()
    paths = [path.resolve() for path in args.audio]
    for path in paths:
        if not path.is_file():
            parser.error(f"Audio file does not exist: {path}")
        if path == args.output.resolve():
            parser.error("The JSON report must not overwrite an audio input.")

    os.environ["HF_HOME"] = str(ROOT / "cache" / "huggingface")
    os.environ["XDG_CACHE_HOME"] = str(ROOT / "cache")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["MKL_NUM_THREADS"] = "2"

    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    import numpy as np

    model_dir = args.model_dir.resolve()
    required = ["model.bin", "config.json", "tokenizer.json"]
    if not all((model_dir / name).is_file() for name in required):
        print(f"Downloading public {MODEL_ID} weights to {model_dir}", file=sys.stderr)
        download_model(
            MODEL_ID,
            output_dir=str(model_dir),
            cache_dir=str(ROOT / "cache" / "huggingface" / "hub"),
            use_auth_token=False,
        )

    model = WhisperModel(
        str(model_dir), device="cpu", compute_type="int8", cpu_threads=2,
        num_workers=1, local_files_only=True,
    )
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL_ID,
        "model_directory": str(model_dir),
        "faster_whisper_version": version("faster-whisper"),
        "compute_type": "int8",
        "cpu_threads": 2,
        "transcription_settings": {
            "language": "en", "task": "transcribe", "temperature": 0.0,
            "beam_size": 5, "word_timestamps": True, "vad_filter": False,
            "condition_on_previous_text": False, "initial_prompt": None,
        },
        "note": "ASR output is an independent intelligibility check, not a human listening assessment.",
        "results": [],
    }
    for path in paths:
        started = time.perf_counter()
        decoded = subprocess.run(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-i', str(path), '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'],
            check=True, capture_output=True,
        )
        samples = np.frombuffer(decoded.stdout, dtype=np.float32).copy()
        generated_segments, info = model.transcribe(
            samples, **report["transcription_settings"],
        )
        segments = []
        for segment in generated_segments:
            segments.append({
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "avg_logprob": segment.avg_logprob,
                "no_speech_probability": segment.no_speech_prob,
                "compression_ratio": segment.compression_ratio,
                "words": [
                    {"word": word.word, "start": word.start, "end": word.end,
                     "probability": word.probability}
                    for word in (segment.words or [])
                ],
            })
        result = {
            "audio_file": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "duration_seconds": info.duration,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "text": "".join(segment["text"] for segment in segments).strip(),
            "segments": segments,
        }
        report["results"].append(result)
        print(f"{path.name}: {result['text']}", file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
