import hashlib
import json
from fastapi import HTTPException

PROCESSING_VERSION = 'semantic-v2-durable-v1'

async def fingerprint_upload(metadata, files):
    hashes = []
    total = 0
    for role, file in files:
        digest = hashlib.sha256()
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > 250 * 1024 * 1024:
                raise HTTPException(413, 'Combined upload exceeds 250 MB')
            digest.update(chunk)
        await file.seek(0)
        hashes.append([role, file.filename, file.content_type, digest.hexdigest()])
    payload = {'metadata': metadata, 'files': hashes, 'processing_version': PROCESSING_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',',':')).encode()).hexdigest()

def scoped_key(scope, key):
    if key is None: return None
    if not key or len(key) > 128: raise HTTPException(400, 'Idempotency-Key must contain 1–128 characters')
    return hashlib.sha256((scope + '\0' + key).encode()).hexdigest()

def lock_number(value):
    return int.from_bytes(bytes.fromhex(value)[:8], 'big', signed=True)
