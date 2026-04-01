from pydantic import BaseModel


class AnalyticsResponse(BaseModel):
    validation_overview: list[dict]
    pipeline_health: list[dict]
    processing_latency: list[dict]
    stage_failure_breakdown: list[dict]
    retry_hotspots: list[dict]
    lowest_accuracy_lectures: list[dict]
    most_incorrect_topics: list[dict]
    lectures_blocked_from_kb: list[dict]
    validation_source_split: list[dict]
    coverage_gaps: list[dict]
    trends: list[dict]
