"""Generate two reference-conditioned Turbo takes with conversational wording."""
import json
import os
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for key, value in {
    'HF_HOME': ROOT / 'cache' / 'huggingface',
    'XDG_CACHE_HOME': ROOT / 'cache',
    'TORCH_HOME': ROOT / 'cache' / 'torch',
    'NUMBA_CACHE_DIR': ROOT / 'cache' / 'numba',
    'MPLCONFIGDIR': ROOT / 'cache' / 'matplotlib',
}.items():
    os.environ[key] = str(value)
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['OMP_NUM_THREADS'] = '2'
os.environ['MKL_NUM_THREADS'] = '2'

import numpy as np
import soundfile as sf
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS

SCRIPT = (
    "Here's the key point. Higher render quality alone won't make a basic model "
    "look cinematic. Character design, textures, lighting, and animation all "
    "need to work together."
)
TAKES = [
    {'name': 'render-quality-natural-v2', 'seed': 42, 'temperature': 0.70, 'top_p': 0.90},
    {'name': 'render-quality-expressive-v2', 'seed': 123, 'temperature': 0.85, 'top_p': 0.95},
]


def main():
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    destination = ROOT / 'generated'
    destination.mkdir(exist_ok=True)
    (ROOT / 'scripts' / 'render-quality-natural-v2.txt').write_text(SCRIPT + '\n')
    started = time.perf_counter()
    print('Loading local Turbo model on CPU.', flush=True)
    model = ChatterboxTurboTTS.from_local(ROOT / 'models' / 'turbo', device='cpu', nano=False)
    with torch.inference_mode():
        model.prepare_conditionals(str(ROOT / 'references' / 'uploaded-reference.wav'))
        for take in TAKES:
            print('Generating ' + take['name'], flush=True)
            random.seed(take['seed'])
            np.random.seed(take['seed'])
            torch.manual_seed(take['seed'])
            take_start = time.perf_counter()
            audio = model.generate(
                SCRIPT, temperature=take['temperature'], top_p=take['top_p'],
                repetition_penalty=1.2,
            ).squeeze().cpu().numpy()
            if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all() or not np.any(audio):
                raise RuntimeError('Invalid generated audio: ' + take['name'])
            peak = float(np.max(np.abs(audio)))
            gain = min(1.0, 0.89125 / peak)
            audio = audio * gain
            path = destination / (take['name'] + '.wav')
            sf.write(path, audio, model.sr, subtype='PCM_16')
            metadata = {
                **take, 'text': SCRIPT, 'model': 'Chatterbox Turbo',
                'sample_rate': model.sr, 'duration_seconds': len(audio) / model.sr,
                'generation_seconds': time.perf_counter() - take_start,
                'peak_protection_gain': gain, 'watermark': 'upstream retained',
            }
            path.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
            print(json.dumps(metadata), flush=True)
    print(f'Finished in {time.perf_counter() - started:.1f} seconds.', flush=True)


if __name__ == '__main__':
    main()
