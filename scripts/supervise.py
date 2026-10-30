"""One free web service can supervise an API and one durable worker.

The API starts first and the worker only once the API answers /health. On a
0.1-CPU Free instance both processes importing at once roughly doubled the
cold start (measured under a 0.1-CPU cap: ~44 s together, ~21 s API alone),
and students wait for the API, not for the worker.
"""
import os
import signal
import subprocess
import sys
import time
import urllib.request

API_READY_TIMEOUT_SECONDS = 120

children=[]
stopping=False
def stop(signum=None, frame=None):
    global stopping
    if stopping: return
    stopping=True
    for child in children:
        if child.poll() is None: child.terminate()

def api_ready(port):
    try: return urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1).status == 200
    except Exception: return False

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    port=os.getenv('PORT','8000')
    api=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','0.0.0.0','--port',port,'--workers','1'])
    children.append(api)
    deadline=time.monotonic()+API_READY_TIMEOUT_SECONDS
    while not stopping and api.poll() is None and not api_ready(port) and time.monotonic()<deadline: time.sleep(.25)
    if not stopping and api.poll() is None:
        children.append(subprocess.Popen([sys.executable,'-m','arq','app.workers.queue.WorkerSettings']))
    while not stopping and all(child.poll() is None for child in children): time.sleep(.2)
    failed=not stopping
finally:
    stop()
    deadline=time.monotonic()+25
    for child in children:
        try: child.wait(timeout=max(.1,deadline-time.monotonic()))
        except subprocess.TimeoutExpired: child.kill();child.wait()
sys.exit(1 if failed else 0)
