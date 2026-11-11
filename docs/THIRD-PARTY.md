# Model and dependency sources

This repository contains the application and orchestration code. Third-party
model/library code is installed from its pinned upstream source; model weights
are downloaded separately. Respect each upstream project's current license and
usage terms when redistributing or building a commercial service.

- Chatterbox source: https://github.com/resemble-ai/chatterbox
  - Revision: `ab55923c27757c90cfcc407baa57dc25a7d25217`
- Approved Turbo model: https://huggingface.co/ResembleAI/chatterbox-turbo
  - Revision: `749d1c1a46eb10492095d68fbcf55691ccf137cd`
- Optional Nano model: https://huggingface.co/ResembleAI/chatterbox-nano
  - Revision: `71ccd1d0081b430592cea481f4307e764e07bc64`
- Perth audio watermark library: https://github.com/resemble-ai/Perth
  - The working revision is pinned in `voice-tools/requirements.lock.txt`.
- CPU PyTorch wheels: https://download.pytorch.org/whl/cpu
- Speech-recognition check: https://github.com/SYSTRAN/faster-whisper
  and https://huggingface.co/Systran/faster-whisper-base.en
- System audio decoder/encoder: https://ffmpeg.org/

The application's original upstream model watermarking is retained.
No third-party API credentials or private training data are distributed here.
