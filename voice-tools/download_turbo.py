import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ['HF_HOME'] = str(ROOT / 'cache' / 'huggingface')
os.environ['HF_HUB_DISABLE_XET'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
from huggingface_hub import snapshot_download

target = ROOT / 'models' / 'turbo'
target.mkdir(parents=True, exist_ok=True)
# Reuse shared files when Nano is present; otherwise download Turbo directly.
shared = ['s3gen_meanflow.safetensors', 've.safetensors', 'conds.pt']
missing_shared = []
for name in shared:
    destination = target / name
    if not destination.exists():
        source = ROOT / 'models' / 'nano' / name
        if source.exists():
            try:
                os.link(source, destination)
            except OSError:
                missing_shared.append(name)
        else:
            missing_shared.append(name)
snapshot_download(
    repo_id='ResembleAI/chatterbox-turbo',
    revision='749d1c1a46eb10492095d68fbcf55691ccf137cd',
    local_dir=target,
    allow_patterns=['t3_turbo_v1.safetensors', '*.json', '*.txt', 'README.md', *missing_shared],
    token=False,
    max_workers=2,
)
print('Turbo model ready:', target)
