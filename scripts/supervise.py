"""One free web service can supervise an API and one durable worker."""
import os
import signal
import subprocess
import sys
import time

children=[]
stopping=False
def stop(signum=None, frame=None):
    global stopping
    if stopping: return
    stopping=True
    for child in children:
        if child.poll() is None: child.terminate()

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    children.append(subprocess.Popen([sys.executable,'-m','arq','app.workers.queue.WorkerSettings']))
    children.append(subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','0.0.0.0','--port',os.getenv('PORT','8000'),'--workers','1']))
    while not stopping and all(child.poll() is None for child in children): time.sleep(.2)
    failed=not stopping
finally:
    stop()
    deadline=time.monotonic()+25
    for child in children:
        try: child.wait(timeout=max(.1,deadline-time.monotonic()))
        except subprocess.TimeoutExpired: child.kill();child.wait()
sys.exit(1 if failed else 0)
