"""Opt-in real Supabase adapter test; touches only fresh disposable fixture keys."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.storage import StorageService, settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', help='Upload/read/delete newly created disposable fixtures')
    parser.add_argument('--output', type=Path, default=Path('artifacts/live-storage-check.json'))
    args = parser.parse_args()
    if not args.run:
        raise SystemExit('Explicit --run required for provider fixture writes')
    if not settings.use_supabase_storage:
        raise SystemExit('Private Supabase adapter configuration required')
    service = StorageService()
    payload = b'EduSense disposable storage integration fixture\n'
    checks = []
    with tempfile.TemporaryDirectory(prefix='edusense-storage-check-') as folder:
        root = Path(folder)
        source = root / 'fixture.txt'
        source.write_bytes(payload)
        for bucket in [settings.supabase_lecture_bucket, settings.supabase_reference_bucket]:
            key = f'integration-checks/{uuid.uuid4().hex}.txt'
            created = False
            try:
                metadata = service._upload_to_supabase(local_path=source, bucket=bucket, object_path=key, content_type='text/plain')
                created = True
                assert metadata['storage_backend'] == 'supabase'
                downloaded = service._download_from_supabase(bucket=bucket, object_path=key, destination=root / 'download.txt')
                assert downloaded.read_bytes() == payload
                public = requests.get(f'{settings.supabase_url.rstrip("/")}/storage/v1/object/public/{bucket}/{key}', timeout=30)
                assert public.status_code != 200, 'Private fixture must not be publicly downloadable'
                checks.append({'bucket': bucket, 'round_trip_sha256': hashlib.sha256(payload).hexdigest(),
                               'private_access_verified': True, 'public_http_status': public.status_code})
            finally:
                if created:
                    service._delete_from_supabase(bucket=bucket, object_path=key)
    report = {'scope': 'real private Supabase upload/download/cleanup using newly created synthetic fixtures', 'checks': checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
