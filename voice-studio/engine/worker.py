#!/usr/bin/env python3
"""Private Sites queue consumer for the already-installed Chatterbox Turbo model."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import gc
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import tempfile
import textwrap
import threading
import time
from urllib.parse import urlparse
import uuid
import wave

ROOT = Path(os.environ.get('VOICE_LAB_ROOT', str(Path(__file__).resolve().parents[2] / 'voice-tools'))).resolve()
JOBS_ROOT = ROOT / 'site-jobs'
MODEL_REVISION = '749d1c1a46eb10492095d68fbcf55691ccf137cd'
MAX_UPLOAD = 20 * 1024 * 1024
MAX_SCRIPT_CHARS = 40000
MAX_SCRIPT_WORDS = 6000
SAMPLE_RATE = 24000
PRESETS = {
    'balanced': {'seed': 42, 'temperature': 0.70, 'top_p': 0.90, 'repetition_penalty': 1.2},
    'expressive': {'seed': 123, 'temperature': 0.85, 'top_p': 0.95, 'repetition_penalty': 1.2},
}
STOP = threading.Event()


class JobError(Exception):
    """An actionable message that is safe to display to the owner."""


class Cancelled(Exception):
    pass


class LeaseLost(Exception):
    pass


class Shutdown(Exception):
    pass


class AuthenticationError(Exception):
    pass


def log(message: str, job_id: str | None = None) -> None:
    entry = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'message': message}
    if job_id:
        entry['jobId'] = job_id
    print(json.dumps(entry), flush=True)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def normalized_script(script: str) -> str:
    return re.sub(r'\s+', ' ', script.lstrip('\ufeff')).strip()


def split_script(script: str, limit: int = 250) -> list[str]:
    """Prefer whole sentences and words, preserving every non-whitespace character."""
    if limit < 20:
        raise ValueError('Chunk limit is too small.')
    script = script.lstrip('\ufeff')
    chunks: list[str] = []
    for paragraph in re.split(r'\n\s*\n', script):
        paragraph = normalized_script(paragraph)
        if not paragraph:
            continue
        pending = ''
        for sentence in re.split(r'(?<=[.!?])\s+', paragraph):
            for part in textwrap.wrap(sentence, width=limit, break_long_words=True,
                                      break_on_hyphens=False, replace_whitespace=False):
                candidate = f'{pending} {part}' if pending else part
                if len(candidate) <= limit:
                    pending = candidate
                else:
                    if pending:
                        chunks.append(pending)
                    pending = part
        if pending:
            chunks.append(pending)
    if re.sub(r'\s', '', ''.join(chunks)) != re.sub(r'\s', '', script):
        raise JobError('The script could not be split without changing its text.')
    if any(len(chunk) > limit for chunk in chunks):
        raise JobError('A script section exceeded the generation limit.')
    return chunks


def canonical_job_id(value: object) -> str:
    raw = str(value)
    match = re.fullmatch(r'(\d{13})-([a-f0-9-]{36})', raw)
    if not match:
        raise JobError('The queued narration has an invalid identifier.')
    try:
        suffix = str(uuid.UUID(match.group(2)))
    except (TypeError, ValueError, AttributeError):
        raise JobError('The queued narration has an invalid identifier.') from None
    result = match.group(1) + '-' + suffix
    if result != raw:
        raise JobError('The queued narration has an invalid identifier.')
    return result


def validate_script(script: object) -> str:
    if not isinstance(script, str) or not normalized_script(script):
        raise JobError('Add a script before generating narration.')
    if len(script) > MAX_SCRIPT_CHARS or len(script.split()) > MAX_SCRIPT_WORDS:
        raise JobError('The script exceeds 6,000 words or 40,000 characters. Split it into separate narrations.')
    return script


def wav_info(path: Path, *, check_signal: bool = True) -> dict:
    try:
        with wave.open(str(path), 'rb') as stream:
            frames = stream.getnframes()
            if stream.getnchannels() != 1 or stream.getframerate() != SAMPLE_RATE or stream.getsampwidth() != 2:
                raise JobError('An audio section has an unexpected format.')
            if frames <= 0:
                raise JobError('An audio section is empty.')
            if check_signal:
                nonzero = False
                for block in iter(lambda: stream.readframes(32768), b''):
                    nonzero = nonzero or bool(block.strip(b'\x00'))
                if not nonzero:
                    raise JobError('An audio section contains only silence.')
            return {'frames': frames, 'durationSeconds': frames / SAMPLE_RATE,
                    'sampleRate': SAMPLE_RATE, 'sha256': file_hash(path)}
    except (wave.Error, EOFError, OSError):
        raise JobError('An audio section could not be decoded. Retry this narration.') from None


def valid_checkpoint(path: Path, info: dict) -> bool:
    if not path.is_file() or not info.get('sha256'):
        return False
    try:
        actual = wav_info(path)
        return actual['sha256'] == info['sha256'] and actual['frames'] == info.get('frames')
    except JobError:
        return False


def decode_reference(source: Path, destination: Path) -> dict:
    if source.stat().st_size > MAX_UPLOAD:
        raise JobError('The voice sample is larger than 20 MB.')
    try:
        probe = subprocess.run([
            'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-show_entries', 'format=format_name', '-of', 'json', str(source),
        ], capture_output=True, check=True, timeout=30)
        names = set(json.loads(probe.stdout)['format']['format_name'].split(','))
        if not names.intersection({'wav', 'mp3', 'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2',
                                   'aac', 'flac', 'ogg', 'matroska', 'webm'}):
            raise JobError('Use a WAV, MP3, M4A, MP4, AAC, FLAC, OGG or WebM voice sample.')
        # Decode at most 31 seconds. Anything above 30 seconds is rejected below;
        # accepted samples are never silently shortened.
        subprocess.run([
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:a:0',
            '-vn', '-t', '31', '-ac', '1', '-ar', str(SAMPLE_RATE),
            '-c:a', 'pcm_s16le', str(destination),
        ], capture_output=True, check=True, timeout=60)
    except JobError:
        raise
    except (subprocess.SubprocessError, OSError, KeyError, ValueError):
        raise JobError('The voice sample could not be read. Try a clear WAV, MP3, M4A, MP4, AAC, FLAC, OGG or WebM recording.') from None
    info = wav_info(destination)
    if not 5.0 < info['durationSeconds'] <= 30.0:
        raise JobError('Use a voice sample longer than 5 seconds and no longer than 30 seconds.')
    return info


def ensure_disk_space(script: str, location: Path) -> None:
    estimated_seconds = max(15, len(script.split()) / 130 * 60)
    # Room for chunk WAVs, assembled WAV, MP3 and scratch. Weights already exist.
    required = max(256 * 1024**2, int(estimated_seconds * 112000 + 128 * 1024**2))
    if shutil.disk_usage(location).free < required:
        raise JobError('This workspace is low on storage. Free space before retrying; completed sections are saved.')


class Api:
    def __init__(self, config: dict, worker_id: str):
        import requests
        self.requests = requests
        parsed = urlparse(str(config.get('site_url', '')))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Runtime configuration must use the exact HTTPS Site origin.')
        self.origin = f'{parsed.scheme}://{parsed.netloc}'
        self.headers = {
            'OAI-Sites-Authorization': 'Bearer ' + str(config['site_service_token']),
            'X-Engine-Key': str(config['engine_token']),
            'X-Worker-Id': worker_id,
        }
        if not config['site_service_token'] or not config['engine_token']:
            raise ValueError('The engine runtime configuration is incomplete.')
        self.worker_id = worker_id

    def request(self, method: str, path: str, *, payload: dict | None = None,
                upload: Path | None = None, content_type: str | None = None,
                download: Path | None = None, retry: bool = True):
        if not path.startswith('/api/engine/') or '?' in path or '..' in path:
            raise ValueError('Unexpected engine API route.')
        headers = dict(self.headers)
        if content_type:
            headers['Content-Type'] = content_type
        if payload is not None:
            payload = {**payload, 'workerId': self.worker_id}
        delay = 2
        while not STOP.is_set():
            try:
                with contextlib.ExitStack() as stack:
                    stream = stack.enter_context(upload.open('rb')) if upload else None
                    if upload:
                        headers['Content-Length'] = str(upload.stat().st_size)
                    response = stack.enter_context(self.requests.request(
                        method, self.origin + path, headers=headers, json=payload,
                        data=stream, timeout=(10, 180 if upload or download else 30),
                        allow_redirects=False, stream=bool(download),
                    ))
                    if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                        raise AuthenticationError('The engine cannot authenticate with the private Site.')
                    if response.status_code in (404, 409, 410):
                        raise LeaseLost('The job is no longer assigned to this engine.')
                    if response.status_code == 429 or response.status_code >= 500:
                        if not retry:
                            return None
                        raise self.requests.ConnectionError('Temporary Site error')
                    if not response.ok:
                        raise JobError(f'The Site rejected an engine operation (HTTP {response.status_code}).')
                    if download:
                        if int(response.headers.get('Content-Length', '0') or 0) > MAX_UPLOAD:
                            raise JobError('The voice sample is larger than 20 MB.')
                        temp = download.with_suffix(download.suffix + '.tmp')
                        count = 0
                        try:
                            with temp.open('wb') as destination:
                                for block in response.iter_content(65536):
                                    if STOP.is_set():
                                        raise Shutdown()
                                    count += len(block)
                                    if count > MAX_UPLOAD:
                                        raise JobError('The voice sample is larger than 20 MB.')
                                    destination.write(block)
                            os.replace(temp, download)
                        finally:
                            with contextlib.suppress(FileNotFoundError):
                                temp.unlink()
                        return {'bytes': count}
                    return response.json()
            except (self.requests.ConnectionError, self.requests.Timeout):
                if not retry:
                    return None
                log('Site connection interrupted; waiting to reconnect.')
                STOP.wait(delay)
                delay = min(30, delay * 2)
        raise Shutdown()


class Engine:
    def __init__(self, api: Api, jobs_root: Path = JOBS_ROOT):
        self.api = api
        self.jobs_root = jobs_root
        self.active_job_id: str | None = None
        self.stage = 'ready'
        self.cancel_requested = threading.Event()
        self.model = None
        self.state_lock = threading.Lock()

    def heartbeat(self) -> None:
        while not STOP.is_set():
            with self.state_lock:
                payload = {'activeJobId': self.active_job_id, 'stage': self.stage}
            try:
                result = self.api.request('POST', '/api/engine/heartbeat', payload=payload, retry=False)
                if result and result.get('cancelRequested'):
                    # A delayed response for a finished job must never cancel
                    # the next job that started while this request was in flight.
                    with self.state_lock:
                        if self.active_job_id and self.active_job_id == payload['activeJobId']:
                            self.cancel_requested.set()
            except AuthenticationError:
                log('The engine needs its private Site connection restored.')
                STOP.set()
            except (LeaseLost, JobError, ValueError):
                # The main request will reconcile a stale lease. Do not expose
                # server response bodies or credentials in logs.
                pass
            except Shutdown:
                return
            STOP.wait(15)

    def set_stage(self, stage: str) -> None:
        with self.state_lock:
            self.stage = stage

    def check_cancelled(self) -> None:
        if STOP.is_set():
            raise Shutdown()
        if self.cancel_requested.is_set():
            raise Cancelled()

    def progress(self, job_id: str, manifest: dict, stage: str) -> None:
        self.set_stage(stage)
        completed = sum(1 for chunk in manifest['chunks'] if chunk.get('sha256'))
        duration = sum(chunk.get('durationSeconds', 0) for chunk in manifest['chunks'])
        result = self.api.request('POST', f'/api/engine/jobs/{job_id}/progress', payload={
            'completedChunks': completed, 'totalChunks': len(manifest['chunks']),
            'progress': min(0.97, completed / len(manifest['chunks']) * 0.95),
            'stage': stage, 'durationSeconds': duration,
        })
        if result.get('cancelRequested'):
            self.cancel_requested.set()
        self.check_cancelled()

    def load_model(self):
        for key, value in {
            'HF_HOME': ROOT / 'cache' / 'huggingface',
            'XDG_CACHE_HOME': ROOT / 'cache', 'TORCH_HOME': ROOT / 'cache' / 'torch',
            'NUMBA_CACHE_DIR': ROOT / 'cache' / 'numba',
            'MPLCONFIGDIR': ROOT / 'cache' / 'matplotlib',
        }.items():
            os.environ[key] = str(value)
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                          OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
        import torch
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        torch.set_num_threads(2)
        with contextlib.suppress(RuntimeError):
            torch.set_num_interop_threads(1)
        self.model = ChatterboxTurboTTS.from_local(ROOT / 'models' / 'turbo', device='cpu', nano=False)
        return self.model

    def unload_model(self):
        self.model = None
        gc.collect()

    def run_job(self, job: dict) -> None:
        import numpy as np
        job_id = canonical_job_id(job.get('id'))
        script = validate_script(job.get('script'))
        preset = job.get('preset', 'balanced')
        if preset not in PRESETS:
            raise JobError('Choose balanced or expressive delivery.')
        settings = PRESETS[preset]
        self.cancel_requested.clear()
        with self.state_lock:
            self.active_job_id = job_id
            self.stage = 'Preparing your voice sample'
        directory = self.jobs_root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        ensure_disk_space(script, directory)
        original = directory / 'reference.original'
        self.api.request('GET', f'/api/engine/jobs/{job_id}/reference', download=original)
        reference = directory / 'reference.wav'
        reference_info = decode_reference(original, reference)
        script_hash = hashlib.sha256(script.encode('utf-8')).hexdigest()
        identity = {
            'scriptSha256': script_hash, 'referenceSha256': file_hash(original),
            'preset': preset, 'settings': settings, 'modelRevision': MODEL_REVISION,
        }
        manifest_path = directory / 'manifest.json'
        chunks = split_script(script)
        if not chunks:
            raise JobError('Add some words to your script.')
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        if manifest and manifest.get('identity') != identity:
            raise JobError('This saved job has different inputs or settings. Create a new narration to keep your progress safe.')
        if manifest and [chunk['text'] for chunk in manifest['chunks']] != chunks:
            raise JobError('This saved job uses different script sections. Create a new narration.')
        if not manifest:
            manifest = {
                'jobId': job_id, 'identity': identity, 'referenceDurationSeconds': reference_info['durationSeconds'],
                'status': 'processing', 'createdAt': time.time(),
                'chunks': [{'text': text, 'seed': settings['seed'] + index,
                            'filename': f'chunk-{index:05d}.wav'} for index, text in enumerate(chunks)],
            }
            (directory / 'script.txt').write_text(script, encoding='utf-8')
        for chunk in manifest['chunks']:
            if not valid_checkpoint(directory / chunk['filename'], chunk):
                for key in ('sha256', 'frames', 'durationSeconds', 'sampleRate'):
                    chunk.pop(key, None)
        atomic_json(manifest_path, manifest)
        self.progress(job_id, manifest, 'Loading the voice model')
        model = self.load_model()
        import torch
        with torch.inference_mode():
            model.prepare_conditionals(str(reference))
            for index, chunk in enumerate(manifest['chunks']):
                self.check_cancelled()
                if chunk.get('sha256'):
                    continue
                self.progress(job_id, manifest, f'Generating section {index + 1} of {len(chunks)}')
                random.seed(chunk['seed'])
                np.random.seed(chunk['seed'])
                torch.manual_seed(chunk['seed'])
                audio = model.generate(
                    chunk['text'], temperature=settings['temperature'],
                    top_p=settings['top_p'], repetition_penalty=settings['repetition_penalty'],
                ).squeeze().detach().cpu().numpy()
                if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all() or not np.any(audio):
                    raise JobError('A section could not be generated clearly. Retry to resume the saved narration.')
                peak = float(np.max(np.abs(audio)))
                audio = audio * min(1.0, 0.89125 / peak)
                pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype('<i2')
                output = directory / chunk['filename']
                temporary = output.with_suffix('.tmp')
                with wave.open(str(temporary), 'wb') as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(SAMPLE_RATE)
                    stream.writeframes(pcm.tobytes())
                info = wav_info(temporary)
                os.replace(temporary, output)
                chunk.update(info)
                atomic_json(manifest_path, manifest)
                log(f'Saved section {index + 1} of {len(chunks)}.', job_id)
                self.progress(job_id, manifest, f'Generated {index + 1} of {len(chunks)} sections')
        # Drop heavy weights before encoding/uploading. A new voice is freshly
        # conditioned for the next job rather than sharing the prior conditionals.
        del model
        self.unload_model()
        self.progress(job_id, manifest, 'Joining your narration')
        final_wav = directory / 'narration.wav'
        temp_wav = directory / 'narration.tmp'
        silence = b'\x00\x00' * round(0.16 * SAMPLE_RATE)
        with wave.open(str(temp_wav), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            for index, chunk in enumerate(manifest['chunks']):
                self.check_cancelled()
                if index:
                    output.writeframes(silence)
                with wave.open(str(directory / chunk['filename']), 'rb') as source:
                    for block in iter(lambda: source.readframes(32768), b''):
                        output.writeframes(block)
        os.replace(temp_wav, final_wav)
        output_info = wav_info(final_wav)
        final_mp3 = directory / 'narration.mp3'
        try:
            subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-i', str(final_wav), '-codec:a', 'libmp3lame', '-b:a', '128k', str(final_mp3),
            ], check=True, capture_output=True, timeout=300)
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(final_mp3), '-f', 'null', '-'],
                           check=True, capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            raise JobError('The recording could not be exported. Retry to reuse the saved sections.') from None
        self.progress(job_id, manifest, 'Saving your finished audio')
        if final_wav.stat().st_size > 100 * 1024 * 1024 or final_mp3.stat().st_size > 100 * 1024 * 1024:
            raise JobError('The recording exceeds the Site single-file limit. Split this script into shorter narrations; completed sections are saved.')
        for extension, path, content_type in (
            ('mp3', final_mp3, 'audio/mpeg'), ('wav', final_wav, 'audio/wav'),
        ):
            self.check_cancelled()
            self.api.request('POST', f'/api/engine/jobs/{job_id}/files/{extension}',
                             upload=path, content_type=content_type)
        self.check_cancelled()
        completion = self.api.request('POST', f'/api/engine/jobs/{job_id}/complete', payload={
            'durationSeconds': output_info['durationSeconds'],
            'completedChunks': len(chunks), 'totalChunks': len(chunks),
        })
        if completion.get('status') == 'cancelled' or completion.get('cancelRequested'):
            manifest.update(status='cancelled', cancelledAt=time.time())
            atomic_json(manifest_path, manifest)
            log('Cancellation arrived during upload; completed sections remain saved for resuming.', job_id)
            return
        if completion.get('status') not in (None, 'completed') or not completion.get('ok'):
            raise LeaseLost('The narration completion was not acknowledged.')
        manifest.update(status='completed', completedAt=time.time(), output={
            'wavSha256': output_info['sha256'], 'mp3Sha256': file_hash(final_mp3),
            'durationSeconds': output_info['durationSeconds'],
        })
        # The authenticated Site now owns both outputs. Keep only the compact
        # manifest; failed/cancelled jobs retain audio checkpoints for resuming.
        # An already-acknowledged upload remains successful even if local
        # cleanup is temporarily unavailable. Do not report it as a failed job.
        try:
            atomic_json(manifest_path, manifest)
            for path in directory.iterdir():
                if path.is_file() and path.name != 'manifest.json':
                    path.unlink()
            manifest['localAudioCleanedUp'] = True
            atomic_json(manifest_path, manifest)
        except OSError:
            log('Finished audio is uploaded; local cleanup is deferred.', job_id)
        log('Narration completed and uploaded.', job_id)

    def handle_job(self, job: dict) -> None:
        job_id = canonical_job_id(job.get('id'))
        try:
            self.run_job(job)
        except Cancelled:
            log('Narration paused by the owner; completed sections are saved.', job_id)
            self.api.request('POST', f'/api/engine/jobs/{job_id}/cancelled', payload={})
        except (LeaseLost, Shutdown):
            log('Narration stopped; completed sections remain available to resume.', job_id)
            raise
        except AuthenticationError:
            raise
        except Exception as error:
            message = str(error) if isinstance(error, JobError) else 'Generation stopped unexpectedly. Retry to resume the saved sections.'
            log(message, job_id)
            self.api.request('POST', f'/api/engine/jobs/{job_id}/fail', payload={'error': message})
        finally:
            self.unload_model()
            self.cancel_requested.clear()
            with self.state_lock:
                self.active_job_id = None
                self.stage = 'ready'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).parent / '.runtime.json')
    parser.add_argument('--once', action='store_true', help='Claim and process at most one job, then exit.')
    args = parser.parse_args()
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(JOBS_ROOT, 0o700)
    lock = (JOBS_ROOT / '.engine.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('A voice engine is already running in this workspace.')
        return 0
    try:
        config = json.loads(args.config.read_text())
        worker_file = JOBS_ROOT / 'worker.id'
        if worker_file.exists():
            worker_id = str(uuid.UUID(worker_file.read_text().strip()))
        else:
            worker_id = str(uuid.uuid4())
            worker_file.write_text(worker_id + '\n')
            os.chmod(worker_file, 0o600)
        api = Api(config, worker_id)
    except (OSError, ValueError, KeyError):
        log('The engine runtime configuration is missing or invalid.')
        return 1
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    engine = Engine(api)
    heartbeat = threading.Thread(target=engine.heartbeat, name='voice-heartbeat', daemon=True)
    heartbeat.start()
    log('Voice engine started.')
    try:
        while not STOP.is_set():
            try:
                response = api.request('POST', '/api/engine/claim', payload={})
                job = response.get('job')
                if job:
                    engine.handle_job(job)
                if args.once:
                    break
                if not job:
                    STOP.wait(5)
            except LeaseLost:
                if args.once:
                    break
                STOP.wait(2)
    except AuthenticationError:
        log('The private Site connection must be restored before generation can continue.')
        return 1
    except Shutdown:
        pass
    finally:
        STOP.set()
        engine.unload_model()
        heartbeat.join(timeout=2)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
