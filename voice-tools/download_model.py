"""Download the public Chatterbox Nano files used by the CPU voice tool."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import snapshot_download

path = snapshot_download(
    repo_id="ResembleAI/chatterbox-nano",
    revision="71ccd1d0081b430592cea481f4307e764e07bc64",
    local_dir=ROOT / "models" / "nano",
    allow_patterns=[
        "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors",
        "ve.safetensors", "conds.pt", "*.json", "*.txt", "README.md",
    ],
    max_workers=2,
    token=False,
)
print(f"Model ready: {path}")
