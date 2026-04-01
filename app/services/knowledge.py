from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk
from app.models.transcript import TopicSegment, TranscriptSegment
from app.services.embedding import EmbeddingService


class KnowledgeService:
    def __init__(self) -> None:
        self.embedding_service = EmbeddingService()

    async def rebuild_for_lecture(
        self,
        session: AsyncSession,
        lecture_id,
        topics: list[TopicSegment],
        transcript_segments: list[TranscriptSegment],
    ) -> None:
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.lecture_id == lecture_id))

        content_blocks: list[tuple[str, str, dict]] = []
        segments_by_topic: dict[int, list[TranscriptSegment]] = defaultdict(list)
        ordered_topics = sorted(topics, key=lambda item: item.sequence)
        ordered_segments = sorted(transcript_segments, key=lambda item: item.sequence)

        full_lecture_content = " ".join((segment.edited_text or segment.text) for segment in ordered_segments).strip()
        if full_lecture_content:
            content_blocks.append(
                (
                    "Full Lecture",
                    full_lecture_content,
                    {
                        "kind": "lecture_full",
                        "segment_count": len(ordered_segments),
                        "student_visible": False,
                        "approved_for_kb": False,
                    },
                )
            )

        for index, topic in enumerate(ordered_topics):
            next_topic = ordered_topics[index + 1] if index + 1 < len(ordered_topics) else None
            for segment in ordered_segments:
                if segment.start_time < topic.start_time:
                    continue
                if next_topic is not None and segment.start_time >= next_topic.start_time:
                    continue
                segments_by_topic[topic.sequence].append(segment)

        for topic in topics:
            supporting_segments = segments_by_topic.get(topic.sequence, [])
            topic_content = " ".join((segment.edited_text or segment.text) for segment in supporting_segments).strip()
            content_blocks.append(
                (
                    topic.title,
                    topic_content or topic.summary,
                    {
                        "kind": "topic",
                        "sequence": topic.sequence,
                        "topic_id": str(topic.id),
                        "start_time": topic.start_time,
                        "end_time": topic.end_time,
                        "summary": topic.summary,
                        "validation_state": topic.validation_state.value,
                        "approved_for_kb": topic.approved_for_kb,
                        "student_visible": topic.approved_for_kb and topic.validation_state.value == "safe",
                    },
                )
            )

            for support_index in range(0, len(supporting_segments), 3):
                window = supporting_segments[support_index:support_index + 3]
                if not window:
                    continue
                content_blocks.append(
                    (
                        topic.title,
                        " ".join((segment.edited_text or segment.text) for segment in window),
                        {
                            "kind": "topic_support",
                            "topic_sequence": topic.sequence,
                            "topic_id": str(topic.id),
                            "support_index": support_index // 3 + 1,
                            "start_time": window[0].start_time,
                            "end_time": window[-1].end_time,
                            "validation_state": topic.validation_state.value,
                            "approved_for_kb": topic.approved_for_kb,
                            "student_visible": topic.approved_for_kb and topic.validation_state.value == "safe",
                        },
                    )
                )

        embeddings = self.embedding_service.encode([content for _, content, _ in content_blocks]) if content_blocks else []
        for (topic, content, metadata), embedding in zip(content_blocks, embeddings, strict=True):
            session.add(
                KnowledgeChunk(
                    lecture_id=lecture_id,
                    topic=topic,
                    content=content,
                    details=metadata,
                    embedding=embedding,
                )
            )

    async def search(
        self,
        session: AsyncSession,
        query: str,
        topic: str | None = None,
        limit: int = 10,
        lecture_id=None,
        approved_only: bool = False,
    ) -> list[KnowledgeChunk]:
        query_embedding = self.embedding_service.encode([query])[0]
        stmt = select(KnowledgeChunk)
        if topic:
            stmt = stmt.where(KnowledgeChunk.topic.ilike(f"%{topic}%"))
        if lecture_id is not None:
            stmt = stmt.where(KnowledgeChunk.lecture_id == lecture_id)
        if approved_only:
            stmt = stmt.where(KnowledgeChunk.details["student_visible"].astext == "true")

        stmt = stmt.order_by(KnowledgeChunk.embedding.cosine_distance(query_embedding)).limit(limit)
        result = await session.scalars(stmt)
        return list(result)

    async def sync_topic_visibility(self, session: AsyncSession, topic: TopicSegment) -> None:
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.lecture_id == topic.lecture_id)
        chunks = list(await session.scalars(stmt))
        topic_id = str(topic.id)
        student_visible = topic.approved_for_kb and topic.validation_state.value == "safe"

        for chunk in chunks:
            details = dict(chunk.details or {})
            if details.get("topic_id") != topic_id:
                continue
            details["validation_state"] = topic.validation_state.value
            details["approved_for_kb"] = topic.approved_for_kb
            details["student_visible"] = student_visible
            chunk.details = details
