from app.models.lecture import LectureStatus


def _coerce_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def derive_accuracy_score(
    *,
    stored_accuracy: float | None,
    metrics: dict | None,
    claim_count: int | None = None,
    status: LectureStatus | None = None,
) -> float | None:
    safe_metrics = metrics if isinstance(metrics, dict) else {}
    candidate_count = _coerce_int(safe_metrics.get("fact_check_candidates"))
    false_count = claim_count
    if false_count is None:
        false_count = _coerce_int(safe_metrics.get("fact_check_false_claims"))
    if false_count is None:
        false_count = _coerce_int(safe_metrics.get("claims")) or 0

    if candidate_count is not None:
        if candidate_count <= 0:
            return 100.0
        bounded_false_count = min(max(false_count or 0, 0), candidate_count)
        return round(((candidate_count - bounded_false_count) / candidate_count) * 100, 2)

    semantic_version = safe_metrics.get("semantic_pipeline_version")
    if (
        status == LectureStatus.completed
        and isinstance(semantic_version, str)
        and semantic_version.startswith("v2")
        and (false_count or 0) == 0
    ):
        return 100.0

    return stored_accuracy
