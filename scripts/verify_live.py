"""Read-only live health/auth/pagination checks. Never emits tokens or lecture data."""
import argparse
import base64
import hmac
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
settings = get_settings()
if not settings.internal_api_key:
    raise SystemExit('INTERNAL_API_KEY must be configured locally')

def token(role):
    payload = base64.urlsafe_b64encode(json.dumps({
        'email': 'deployment-check@example.invalid', 'role': role, 'exp': int(time.time()) + 180,
    }).encode()).rstrip(b'=')
    signature = base64.urlsafe_b64encode(hmac.digest(settings.internal_api_key.encode(), payload, 'sha256')).rstrip(b'=')
    return (payload + b'.' + signature).decode()

report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'base_url': args.base_url,
          'scope': 'read-only serving dependencies, signed authorization, bounded lecture pagination; no provider calls or uploads', 'checks': []}
with httpx.Client(base_url=args.base_url, timeout=120) as client:
    admin = token('admin')
    student = token('student')
    cases = [('/health', None, 200), ('/ready', None, 200),
             ('/api/v1/processing?limit=1', None, 401),
             ('/api/v1/processing?limit=1', student, 403),
             ('/api/v1/processing?limit=1', admin, 200),
             ('/api/v1/processing?limit=101', admin, 422),
             ('/api/v1/catalog', admin + 'invalid', 401),
             ('/api/v1/student/subjects', student, 200)]
    for path, credential, expected in cases:
        response = client.get(path, headers={'Authorization': 'Bearer ' + credential} if credential else {})
        entry = {'path': path, 'identity': 'anonymous' if credential is None else ('student' if credential == student else 'signed check'),
                 'status': response.status_code, 'expected': expected, 'passed': response.status_code == expected,
                 'request_id_present': bool(response.headers.get('x-request-id'))}
        if path == '/api/v1/processing?limit=1' and expected == 200:
            entry['bounded_result'] = isinstance(response.json(), list) and len(response.json()) <= 1
            entry['passed'] &= entry['bounded_result']
        report['checks'].append(entry)
report['passed'] = all(item['passed'] and item['request_id_present'] for item in report['checks'])
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
if not report['passed']:
    raise SystemExit(1)
