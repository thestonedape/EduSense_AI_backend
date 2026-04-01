import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import aiofiles
from fastapi import UploadFile
import requests

from app.core.config import get_settings


settings = get_settings()


class StorageServiceError(Exception):
    pass


@dataclass
class StoredFileResult:
    stored_name: str
    local_path: str
    metadata: dict


class StorageService:
    def __init__(self) -> None:
        self.upload_dir = Path(settings.upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def _save_local_copy(self, file: UploadFile) -> tuple[str, Path]:
        suffix = Path(file.filename or "").suffix
        stored_name = f"{uuid4()}{suffix}"
        destination = self.upload_dir / stored_name

        async with aiofiles.open(destination, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                await output.write(chunk)

        await file.seek(0)
        return stored_name, destination.resolve()

    def _upload_to_supabase(self, *, local_path: Path, bucket: str, object_path: str, content_type: str | None) -> dict:
        url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
            "x-upsert": "true",
        }
        if content_type:
            headers["Content-Type"] = content_type
        with local_path.open("rb") as payload:
            response = requests.post(url, headers=headers, data=payload, timeout=60)
        if not response.ok:
            detail = response.text.strip() or response.reason or "Supabase storage upload failed."
            raise StorageServiceError(
                f"Supabase storage upload failed for bucket '{bucket}' and object '{object_path}'. "
                f"Status {response.status_code}. Response: {detail}"
            )
        return {
            "storage_backend": "supabase",
            "supabase_bucket": bucket,
            "supabase_object_path": object_path,
            "supabase_public_url": f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}",
        }

    def _download_from_supabase(self, *, bucket: str, object_path: str, destination: Path) -> Path:
        url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        }
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination.resolve()

    def _delete_from_supabase(self, *, bucket: str, object_path: str) -> None:
        url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{object_path}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        }
        response = requests.delete(url, headers=headers, timeout=60)
        response.raise_for_status()

    def ensure_local_path(self, storage_path: str, metadata: dict | None = None) -> str:
        local_path = Path(storage_path)
        if local_path.exists():
            return str(local_path.resolve())

        safe_metadata = metadata if isinstance(metadata, dict) else {}
        if safe_metadata.get("storage_backend") != "supabase":
            raise FileNotFoundError(f"Local file is missing and no Supabase fallback is available: {storage_path}")

        bucket = str(safe_metadata.get("supabase_bucket") or "").strip()
        object_path = str(safe_metadata.get("supabase_object_path") or "").strip()
        cached_path_value = str(safe_metadata.get("local_cached_path") or storage_path).strip()
        if not bucket or not object_path:
            raise FileNotFoundError(f"Supabase metadata is incomplete for missing file: {storage_path}")
        if not settings.use_supabase_storage:
            raise RuntimeError("Supabase storage fallback requested but Supabase storage is not configured.")

        destination = Path(cached_path_value)
        return str(self._download_from_supabase(bucket=bucket, object_path=object_path, destination=destination))

    async def _persist_file(self, file: UploadFile, *, bucket: str, prefix: str) -> StoredFileResult:
        if settings.use_supabase_storage and not bucket.strip():
            raise StorageServiceError("Supabase storage is enabled, but the target bucket name is empty.")
        stored_name, local_path = await self._save_local_copy(file)
        metadata = {
            "storage_backend": "local",
            "local_cached_path": str(local_path),
            "stored_name": stored_name,
        }
        if settings.use_supabase_storage:
            object_path = f"{prefix}/{stored_name}"
            supabase_metadata = await asyncio.to_thread(
                self._upload_to_supabase,
                local_path=local_path,
                bucket=bucket,
                object_path=object_path,
                content_type=file.content_type,
            )
            metadata.update(supabase_metadata)
        return StoredFileResult(
            stored_name=stored_name,
            local_path=str(local_path),
            metadata=metadata,
        )

    async def save_upload(self, file: UploadFile) -> StoredFileResult:
        return await self._persist_file(file, bucket=settings.supabase_lecture_bucket, prefix="lectures")

    async def save_reference_upload(self, file: UploadFile) -> StoredFileResult:
        return await self._persist_file(file, bucket=settings.supabase_reference_bucket, prefix="references")

    async def cleanup_file(self, file_path: str, metadata: dict | None = None) -> None:
        safe_metadata = metadata if isinstance(metadata, dict) else {}
        local_path = Path(file_path)
        if local_path.exists():
            try:
                local_path.unlink()
            except FileNotFoundError:
                pass

        if safe_metadata.get("storage_backend") == "supabase":
            bucket = str(safe_metadata.get("supabase_bucket") or "").strip()
            object_path = str(safe_metadata.get("supabase_object_path") or "").strip()
            if bucket and object_path and settings.use_supabase_storage:
                try:
                    await asyncio.to_thread(
                        self._delete_from_supabase,
                        bucket=bucket,
                        object_path=object_path,
                    )
                except Exception:
                    # Best-effort cleanup only. DB rollback is the important boundary.
                    pass
