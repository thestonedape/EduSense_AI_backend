import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from redis import Redis

pytestmark = pytest.mark.skipif(not os.getenv('SDE_TEST_DATABASE_URL'), reason='Explicit isolated test database required')

ROOT = Path(__file__).resolve().parents[1]
WORKER_KEY = 'edusense:worker:ready'


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _healthy(port):
    try:
        return urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1).status == 200
    except Exception:
        return False


def test_api_answers_before_the_worker_starts_and_both_stop_cleanly():
    redis = Redis.from_url(os.environ['REDIS_URL'])
    redis.delete(WORKER_KEY)
    port = _free_port()
    proc = subprocess.Popen([sys.executable, 'scripts/supervise.py'], cwd=ROOT, env={**os.environ, 'PORT': str(port)})
    try:
        deadline = time.monotonic() + 60
        while not _healthy(port):
            assert proc.poll() is None, 'supervisor exited before the API became healthy'
            assert time.monotonic() < deadline, 'API never became healthy'
            time.sleep(0.05)
        # The worker is only launched after this point, so it cannot have registered yet.
        assert not redis.exists(WORKER_KEY)

        deadline = time.monotonic() + 60
        while not redis.exists(WORKER_KEY):
            assert proc.poll() is None, 'supervisor exited before the worker started'
            assert time.monotonic() < deadline, 'worker never registered'
            time.sleep(0.2)
        assert _healthy(port)
    finally:
        proc.send_signal(signal.SIGTERM)
        exit_code = proc.wait(timeout=40)
    assert exit_code == 0
    assert not redis.exists(WORKER_KEY)


def test_supervisor_fails_without_starting_the_worker_when_the_api_cannot_boot(tmp_path):
    redis = Redis.from_url(os.environ['REDIS_URL'])
    redis.delete(WORKER_KEY)
    env = {**os.environ, 'PORT': str(_free_port()), 'DATABASE_URL': 'postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none'}
    proc = subprocess.Popen([sys.executable, 'scripts/supervise.py'], cwd=ROOT, env=env)
    assert proc.wait(timeout=90) == 1
    assert not redis.exists(WORKER_KEY)
