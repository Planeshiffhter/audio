"""Receive scoped credentials on hidden stdin; never include them in source or output."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import termios

if sys.stdin.isatty():
    settings = termios.tcgetattr(sys.stdin.fileno())
    settings[3] &= ~termios.ECHO
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, settings)
print('Ready for private JSON on stdin (input is hidden).', flush=True)
data = json.loads(sys.stdin.readline())
root = Path(__file__).resolve().parents[1]
if data['action'] == 'configure_engine':
    target = root / 'engine' / '.runtime.json'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data['config'], f)
    print('Private engine configuration saved.', flush=True)
elif data['action'] == 'source_git':
    credential = data['credential']
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_CONFIG_COUNT'] = '1'
    env['GIT_CONFIG_KEY_0'] = 'http.extraHeader'
    env['GIT_CONFIG_VALUE_0'] = 'Authorization: Bearer ' + credential['token']
    remote = credential['remote_url']
    operation = data.get('operation', 'push')
    if operation == 'probe':
        command = ['git', 'ls-remote', remote]
    elif operation == 'push':
        command = ['git', 'push', remote, 'HEAD:' + credential['branch']]
    else:
        raise ValueError('Unsupported operation')
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    # Git can include remote error text; redact the scoped credential before printing.
    output = (result.stdout + result.stderr).replace(credential['token'], '[redacted]')
    print(output, flush=True)
    raise SystemExit(result.returncode)
else:
    raise ValueError('Unsupported action')
