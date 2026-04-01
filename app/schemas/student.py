from uuid import UUID

from pydantic import BaseModel


class StudentSubjectSummary(BaseModel):
    id: str
    name: str
    code: str
    department_name: str | None = None
    program_name: str | None = None
    lecture_count: int
    reference_count: int
    description: str
    latest_lecture_date: str | None = None


class StudentLectureSummary(BaseModel):
    id: UUID
    subject_id: str
    lecture_name: str
    lecture_number: int | None = None
    lecture_date: str | None = None
    faculty_name: str | None = None
    summary: str
    topic_count: int
    reference_count: int
    validation_source: str
    progress_status: str | None = None
    last_opened_at: str | None = None


class StudentSubjectDetail(BaseModel):
    subject: StudentSubjectSummary
    lectures: list[StudentLectureSummary]


class StudentTopic(BaseModel):
    id: str
    title: str
    summary: str
    source: str


class StudentLectureDetail(BaseModel):
    id: UUID
    subject_id: str
    lecture_name: str
    subject_name: str | None = None
    subject_code: str | None = None
    department_name: str | None = None
    program_name: str | None = None
    lecture_number: int | None = None
    lecture_date: str | None = None
    faculty_name: str | None = None
    summary: str
    reference_files: list[str]
    topics: list[StudentTopic]
    recommended_questions: list[str]
    validation_source: str


class StudentChatRequest(BaseModel):
    message: str
    lecture_id: UUID


class StudentChatCitation(BaseModel):
    topic: str
    source: str
    excerpt: str


class StudentChatResponse(BaseModel):
    response: str
    citations: list[StudentChatCitation]


class StudentDoubtRequest(BaseModel):
    message: str
    subject_id: str | None = None


class StructuredStudyAnswer(BaseModel):
    core_concept: str | None = None
    simple_explanation: str | None = None
    deep_explanation: str | None = None
    example_or_analogy: str | None = None
    key_takeaways: list[str] = []


class StudentDoubtResponse(BaseModel):
    response: str
    citations: list[StudentChatCitation]
    scope_label: str
    structured_answer: StructuredStudyAnswer | None = None


class StudentPracticeQuestion(BaseModel):
    id: str
    question: str
    lecture_id: UUID
    lecture_name: str
    subject_id: str | None = None
    subject_name: str | None = None
    subject_code: str | None = None
    options: list[str]
    answer: int
    explanation: str


class StudentPracticeResponse(BaseModel):
    questions: list[StudentPracticeQuestion]


class StudentDashboardStats(BaseModel):
    tracked_lectures: int
    completed_lectures: int
    quiz_attempts: int
    saved_chats: int


class StudentDashboardResponse(BaseModel):
    stats: StudentDashboardStats
    recent_lectures: list[StudentLectureSummary]


class StudentProgressRequest(BaseModel):
    lecture_id: UUID
    completed: bool = False


class StudentQuizAttemptRequest(BaseModel):
    lecture_id: UUID
    question_id: str
    question: str
    selected_answer: int
    correct_answer: int
    explanation: str | None = None
