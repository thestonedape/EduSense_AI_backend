from app.models.transcript import TopicSegment, TranscriptSegment
from app.services.semantic_pipeline import SentenceUnit, TopicUnit


class TranscriptService:
    def build_segments(self, lecture_id, sentence_units: list[SentenceUnit]) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(
                lecture_id=lecture_id,
                sequence=index,
                start_time=item.start_time,
                end_time=max(item.end_time, item.start_time),
                text=item.text,
            )
            for index, item in enumerate(sentence_units, start=1)
        ]

    def build_topics(self, lecture_id, topic_units: list[TopicUnit]) -> list[TopicSegment]:
        topics: list[TopicSegment] = []
        for index, topic in enumerate(topic_units, start=1):
            if not topic.sentences:
                continue
            topics.append(
                TopicSegment(
                    lecture_id=lecture_id,
                    sequence=index,
                    title=topic.title,
                    start_time=topic.sentences[0].start_time,
                    end_time=topic.sentences[-1].end_time,
                    summary=topic.description,
                )
            )
        return topics
