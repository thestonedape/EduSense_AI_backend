from datetime import date
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dep
from app.models.lecture_content import LectureContentItem, LectureContentRole
from app.models.lecture import Lecture, LectureStatus
from app.models.processing_job import ProcessingJobType
from app.models.reference_file import ReferenceFile
from app.schemas.lecture import UploadResponse
from app.services.processing import ProcessingService
from app.services.storage import StorageService, StorageServiceError


router = APIRouter()
logger = logging.getLogger("app.upload")

ALLOWED_LECTURE_EXTENSIONS = {
    ".mp3", ".mp4", ".wav", ".m4a", ".aac", ".mov", ".mkv", ".webm"
}
ALLOWED_REFERENCE_EXTENSIONS = {".pdf", ".ppt", ".pptx"}


def build_subject_key(department_name: str | None, program_name: str | None, subject_code: str | None, subject_name: str | None) -> str | None:
    raw = " ".join(
        value.strip()
        for value in [department_name or "", program_name or "", subject_code or "", subject_name or ""]
        if value and value.strip()
    )
    if not raw:
        return None
    return "-".join(filter(None, ("".join(character.lower() if character.isalnum() else "-" for character in part).strip("-") for part in raw.split()))).replace("--", "-")


@router.post("/upload", response_model=UploadResponse)
async def upload_lecture(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    reference_files: list[UploadFile] | None = File(default=None),
    additional_content_files: list[UploadFile] | None = File(default=None),
    department_name: str | None = Form(default=None),
    program_name: str | None = Form(default=None),
    subject_name: str | None = Form(default=None),
    subject_code: str | None = Form(default=None),
    lecture_number: int | None = Form(default=None),
    lecture_date: str | None = Form(default=None),
    faculty_name: str | None = Form(default=None),
    course: str | None = Form(default=None),
    module: str | None = Form(default=None),
    lecture_name: str | None = Form(default=None),
    session: AsyncSession = Depends(db_session_dep),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A file is required.")
    lecture_suffix = Path(file.filename).suffix.lower()
    if lecture_suffix not in ALLOWED_LECTURE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported lecture file type. Upload audio/video in mp3, mp4, wav, m4a, aac, mov, mkv, or webm format.",
        )

    parsed_lecture_date = None
    if lecture_date:
        try:
            parsed_lecture_date = date.fromisoformat(lecture_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Lecture date must be in YYYY-MM-DD format.") from exc

    normalized_department = department_name.strip() if department_name else None
    normalized_program = program_name.strip() if program_name else None
    normalized_subject = subject_name.strip() if subject_name else None
    normalized_subject_code = subject_code.strip() if subject_code else None
    normalized_faculty = faculty_name.strip() if faculty_name else None
    normalized_course = (course.strip() if course else "") or normalized_program or normalized_department or "General"
    normalized_module = (module.strip() if module else "") or normalized_subject or "Lecture"

    storage_service = StorageService()
    uploaded_files: list[tuple[str, dict]] = []
    lecture = None
    saved_reference_count = 0
    saved_content_count = 0
    processing_service = ProcessingService()
    job = None

    try:
        upload_result = await storage_service.save_upload(file)
        uploaded_files.append((upload_result.local_path, upload_result.metadata))

        lecture = Lecture(
            lecture_name=lecture_name or file.filename.rsplit(".", 1)[0],
            department_name=normalized_department,
            program_name=normalized_program,
            subject_name=normalized_subject,
            subject_code=normalized_subject_code,
            subject_key=build_subject_key(normalized_department, normalized_program, normalized_subject_code, normalized_subject),
            lecture_number=lecture_number,
            lecture_date=parsed_lecture_date,
            faculty_name=normalized_faculty,
            original_filename=file.filename,
            storage_path=upload_result.local_path,
            course=normalized_course,
            module=normalized_module,
            status=LectureStatus.pending,
            progress=5,
            metrics=upload_result.metadata,
        )
        session.add(lecture)
        await session.flush()
        session.add(
            LectureContentItem(
                lecture_id=lecture.id,
                role=LectureContentRole.lecture_source,
                original_filename=file.filename,
                storage_path=upload_result.local_path,
                file_type=lecture_suffix.removeprefix(".") or "lecture",
                content_type=file.content_type,
                details=upload_result.metadata,
            )
        )
        saved_content_count += 1

        for reference_file in reference_files or []:
            if not reference_file.filename:
                continue
            reference_suffix = Path(reference_file.filename).suffix.lower()
            if reference_suffix not in ALLOWED_REFERENCE_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported reference file type. Upload PDF, PPT, or PPTX files only.",
                )
            reference_upload = await storage_service.save_reference_upload(reference_file)
            uploaded_files.append((reference_upload.local_path, reference_upload.metadata))
            suffix = reference_suffix.removeprefix(".")
            session.add(
                ReferenceFile(
                    lecture_id=lecture.id,
                    original_filename=reference_file.filename,
                    storage_path=reference_upload.local_path,
                    file_type=suffix or "reference",
                    content_type=reference_file.content_type,
                    details=reference_upload.metadata,
                )
            )
            session.add(
                LectureContentItem(
                    lecture_id=lecture.id,
                    role=LectureContentRole.reference_material,
                    original_filename=reference_file.filename,
                    storage_path=reference_upload.local_path,
                    file_type=suffix or "reference",
                    content_type=reference_file.content_type,
                    details=reference_upload.metadata,
                )
            )
            saved_reference_count += 1
            saved_content_count += 1

        for content_file in additional_content_files or []:
            if not content_file.filename:
                continue
            content_suffix = Path(content_file.filename).suffix.lower()
            if content_suffix in ALLOWED_REFERENCE_EXTENSIONS:
                extra_upload = await storage_service.save_reference_upload(content_file)
                uploaded_files.append((extra_upload.local_path, extra_upload.metadata))
                session.add(
                    ReferenceFile(
                        lecture_id=lecture.id,
                        original_filename=content_file.filename,
                        storage_path=extra_upload.local_path,
                        file_type=content_suffix.removeprefix(".") or "reference",
                        content_type=content_file.content_type,
                        details=extra_upload.metadata,
                    )
                )
                session.add(
                    LectureContentItem(
                        lecture_id=lecture.id,
                        role=LectureContentRole.reference_material,
                        original_filename=content_file.filename,
                        storage_path=extra_upload.local_path,
                        file_type=content_suffix.removeprefix(".") or "reference",
                        content_type=content_file.content_type,
                        details=extra_upload.metadata,
                    )
                )
                saved_reference_count += 1
                saved_content_count += 1
                continue
            if content_suffix in ALLOWED_LECTURE_EXTENSIONS:
                extra_upload = await storage_service.save_upload(content_file)
                uploaded_files.append((extra_upload.local_path, extra_upload.metadata))
                session.add(
                    LectureContentItem(
                        lecture_id=lecture.id,
                        role=LectureContentRole.lecture_support,
                        original_filename=content_file.filename,
                        storage_path=extra_upload.local_path,
                        file_type=content_suffix.removeprefix(".") or "lecture",
                        content_type=content_file.content_type,
                        details=extra_upload.metadata,
                    )
                )
                saved_content_count += 1
                continue
            raise HTTPException(
                status_code=400,
                detail="Additional content must be audio/video, PDF, PPT, or PPTX.",
            )

        job = await processing_service.create_job(
            session,
            lecture.id,
            job_type=ProcessingJobType.upload_pipeline,
            details={"source": "upload"},
        )
        await session.commit()
        await session.refresh(lecture)
    except HTTPException as exc:
        await session.rollback()
        for file_path, metadata in reversed(uploaded_files):
            await storage_service.cleanup_file(file_path, metadata)
        raise exc
    except StorageServiceError as exc:
        await session.rollback()
        logger.exception("lecture_upload_storage_failed error=%s", exc)
        for file_path, metadata in reversed(uploaded_files):
            await storage_service.cleanup_file(file_path, metadata)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        logger.exception("lecture_upload_failed error=%s", exc)
        for file_path, metadata in reversed(uploaded_files):
            await storage_service.cleanup_file(file_path, metadata)
        raise HTTPException(status_code=500, detail="Lecture upload failed before the record could be saved cleanly.") from exc

    background_tasks.add_task(processing_service.launch_pipeline, lecture.id, job.id if job is not None else None)

    return UploadResponse(
        lecture_id=lecture.id,
        lecture_name=lecture.lecture_name,
        status=lecture.status,
        message="Lecture uploaded successfully and queued for processing.",
        processing_job_id=job.id if job is not None else None,
        reference_file_count=saved_reference_count,
        content_item_count=saved_content_count,
    )
