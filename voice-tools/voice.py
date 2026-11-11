#!/usr/bin/env python3
"""Generate English speech locally with the approved Turbo model or legacy Nano."""

from __future__ import annotations

import argparse
import contextlib
import os
import random
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import wave


BASE_DIR = Path(__file__).resolve().parent
MAX_CHUNK_CHARACTERS = 250
JOIN_SILENCE_SECONDS = 0.16


def split_text(text: str, limit: int = MAX_CHUNK_CHARACTERS) -> list[str]:
    """Keep all non-whitespace text, favoring whole sentences and words."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    pending = ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", normalized):
        pieces = textwrap.wrap(
            sentence,
            width=limit,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=False,
            drop_whitespace=True,
        )
        for piece in pieces:
            candidate = f"{pending} {piece}" if pending else piece
            if len(candidate) <= limit:
                pending = candidate
            else:
                chunks.append(pending)
                pending = piece
    if pending:
        chunks.append(pending)
    if re.sub(r"\s", "", "".join(chunks)) != re.sub(r"\s", "", normalized):
        raise ValueError("Text chunking changed the script; generation stopped.")
    return chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Text to speak.")
    source.add_argument("--script", type=Path, help="UTF-8 text file to speak in full.")
    parser.add_argument(
        "--reference", type=Path,
        help="Voice sample (WAV, MP3, M4A, or another ffmpeg-readable file); over 5 seconds.",
    )
    parser.add_argument("--output", required=True, type=Path, help="Output PCM16 WAV file.")
    parser.add_argument("--model", choices=['turbo', 'nano'], default='turbo')
    parser.add_argument("--preset", choices=['balanced', 'expressive'], default='balanced')
    parser.add_argument("--seed", type=int, help="Optional deterministic base seed.")
    parser.add_argument(
        "--model-dir", type=Path, default=None,
        help="Local checkpoint directory (default: models/<model> next to this script).",
    )
    return parser.parse_args()


def prepare_reference(source: Path, destination: Path) -> float:
    if not source.is_file():
        raise ValueError(f"Reference audio was not found: {source}")
    if shutil.which("ffmpeg") is None:
        raise ValueError("ffmpeg is required to read reference audio.")
    result = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source.resolve()), "-vn", "-ac", "1", "-ar", "24000",
            "-c:a", "pcm_s16le", str(destination),
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise ValueError(f"Could not decode reference audio: {result.stderr.strip()}")
    with wave.open(str(destination), "rb") as sample:
        seconds = sample.getnframes() / sample.getframerate()
    if seconds <= 5.0:
        raise ValueError(
            f"Reference audio is {seconds:.2f} seconds; Nano requires more than 5 seconds. "
            "Use about 10–20 seconds of clean speech."
        )
    return seconds


def run(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    text = args.script.read_text(encoding="utf-8-sig") if args.script else args.text
    chunks = split_text(text)
    if not chunks:
        raise ValueError("The script contains no text.")
    if args.output.suffix.lower() != ".wav":
        raise ValueError("Use a .wav output filename; output is 16-bit PCM WAV.")
    for source in (args.script, args.reference):
        if source and source.resolve() == args.output.resolve():
            raise ValueError("The output must not overwrite the script or reference audio.")
    model_dir = (args.model_dir or BASE_DIR / 'models' / args.model).resolve()
    nano = args.model == 'nano'
    settings = {'temperature': 0.70, 'top_p': 0.90, 'seed': 42} if args.preset == 'balanced' else {'temperature': 0.85, 'top_p': 0.95, 'seed': 123}
    base_seed = args.seed if args.seed is not None else settings['seed']
    required = [
        "ve.safetensors", "t3_nano_v1.safetensors" if nano else "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors",
        "tokenizer_config.json", "vocab.json", "merges.txt",
    ]
    if not args.reference:
        required.append("conds.pt")
    missing = [name for name in required if not (model_dir / name).is_file()]
    if missing:
        raise ValueError(
            f"Missing local model files in {model_dir}: {', '.join(missing)}. "
            "This command does not download models."
        )

    # Every model and tokenizer must already be present locally.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HOME"] = str(BASE_DIR / "cache" / "huggingface")
    os.environ["XDG_CACHE_HOME"] = str(BASE_DIR / "cache")
    os.environ["TORCH_HOME"] = str(BASE_DIR / "cache" / "torch")
    os.environ["MPLCONFIGDIR"] = str(BASE_DIR / "cache" / "matplotlib")
    os.environ["NUMBA_CACHE_DIR"] = str(BASE_DIR / "cache" / "numba")
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["MKL_NUM_THREADS"] = "2"

    import numpy as np
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voice-reference-") as reference_temp:
        reference = None
        if args.reference:
            reference = Path(reference_temp) / "reference.wav"
            seconds = prepare_reference(args.reference, reference)
            print(f"Reference: {seconds:.2f} seconds; conditioning once.", flush=True)
        print(f"Loading local {args.model} model on CPU: {model_dir}", flush=True)
        model = ChatterboxTurboTTS.from_local(model_dir, device="cpu", nano=nano)
        with torch.inference_mode():
            if reference:
                model.prepare_conditionals(str(reference))
            elif model.conds is None:
                raise ValueError("No built-in voice is available; supply --reference.")

            sample_rate = int(model.sr)
            silence = np.zeros(round(sample_rate * JOIN_SILENCE_SECONDS), dtype="<i2")
            total_frames = 0
            clipped_samples = 0
            generation_started = time.perf_counter()
            output_temp = None
            try:
                # Replace the destination only after every chunk succeeds.
                with tempfile.NamedTemporaryFile(
                    prefix=".voice-", suffix=".wav", dir=args.output.parent, delete=False,
                ) as temporary:
                    output_temp = Path(temporary.name)
                with wave.open(str(output_temp), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(sample_rate)
                    for index, chunk in enumerate(chunks, start=1):
                        print(f"Generating chunk {index}/{len(chunks)} ({len(chunk)} characters).", flush=True)
                        random.seed(base_seed + index - 1)
                        np.random.seed(base_seed + index - 1)
                        torch.manual_seed(base_seed + index - 1)
                        audio = model.generate(chunk, temperature=settings['temperature'], top_p=settings['top_p'], repetition_penalty=1.2).detach().cpu().float().numpy()
                        if audio.ndim == 2 and audio.shape[0] == 1:
                            audio = audio[0]
                        if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
                            raise RuntimeError(f"Chunk {index} returned invalid or empty audio.")
                        if not np.any(audio):
                            raise RuntimeError(f"Chunk {index} returned only silence.")
                        clipped_samples += int(np.count_nonzero(np.abs(audio) > 1.0))
                        pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
                        if index > 1:
                            output.writeframes(silence.tobytes())
                            total_frames += silence.size
                        output.writeframes(pcm.tobytes())
                        total_frames += pcm.size
                os.replace(output_temp, args.output)
                output_temp = None
            finally:
                if output_temp is not None:
                    with contextlib.suppress(FileNotFoundError):
                        output_temp.unlink()

    duration = total_frames / sample_rate
    generation_seconds = time.perf_counter() - generation_started
    print(
        f"Saved {args.output.resolve()}\n"
        f"Audio: {duration:.2f} seconds, mono {sample_rate} Hz PCM16; {len(chunks)} chunk(s).\n"
        f"Generation: {generation_seconds:.2f} seconds; total: {time.perf_counter() - started:.2f} seconds.",
        flush=True,
    )
    if clipped_samples:
        print(f"PCM conversion clipped {clipped_samples} samples outside [-1, 1].", file=sys.stderr)


def main() -> int:
    args = parse_args()
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
