from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.services.openrouter import OpenRouterService


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "being", "but", "by", "can", "could",
    "do", "does", "discuss", "for", "from", "had", "has", "have", "if", "in", "into", "is", "it", "its", "let",
    "lets", "may", "might", "more", "most", "must", "now", "of", "on", "or", "our", "so", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "to", "today", "too", "up", "us", "was",
    "we", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your", "move", "based",
}

FILLER_PATTERNS = [
    "um",
    "uh",
    "okay",
    "ok",
    "guys",
    "right",
    "so",
    "you know",
    "basically",
    "actually",
    "hello",
    "hi everyone",
    "welcome",
    "welcome back",
]

BOUNDARY_MARKERS = (
    "now let us",
    "now let's",
    "moving on",
    "the next",
    "another important",
    "coming to",
    "in summary",
    "to summarize",
    "to recap",
    "finally",
    "next,",
    "next ",
    "first,",
    "second,",
    "third,",
)

TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9-]{2,}")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
WORD_PATTERN = re.compile(r"\b[\w'-]+\b")


@dataclass
class RawChunk:
    text: str
    start_time: float
    end_time: float


@dataclass
class SentenceUnit:
    text: str
    start_time: float
    end_time: float
    sequence: int


@dataclass
class TopicUnit:
    sequence: int
    title: str
    description: str
    keywords: list[str]
    content: str
    sentences: list[SentenceUnit]


class SemanticPipelineService:
    def __init__(self) -> None:
        self.openrouter = OpenRouterService()

    def build_from_transcription(self, transcription: dict) -> tuple[str, list[SentenceUnit], list[TopicUnit]]:
        chunks = self._normalize_chunks(transcription)
        return self.build_from_chunks(chunks)

    def build_from_sentences(self, segments: list[tuple[str, float, float]]) -> tuple[str, list[SentenceUnit], list[TopicUnit]]:
        chunks = [
            RawChunk(text=text, start_time=start_time, end_time=end_time)
            for text, start_time, end_time in segments
            if text.strip()
        ]
        return self.build_from_chunks(chunks)

    def build_from_chunks(self, chunks: list[RawChunk]) -> tuple[str, list[SentenceUnit], list[TopicUnit]]:
        sentence_units = self._build_sentence_units(chunks)
        cleaned_text = " ".join(sentence.text for sentence in sentence_units).strip()
        topic_units = self._build_topic_units_with_llm(sentence_units) or self._build_topic_units(sentence_units)
        return cleaned_text, sentence_units, topic_units

    def _normalize_chunks(self, transcription: dict) -> list[RawChunk]:
        chunks = transcription.get("chunks") or []
        if not chunks and transcription.get("text"):
            chunks = [{"text": transcription["text"], "start": 0.0, "end": 0.0}]

        normalized: list[RawChunk] = []
        for item in chunks:
            text = self._clean_fragment(str(item.get("text", "")))
            if not text:
                continue
            start_time = float(item.get("start", 0.0))
            end_time = max(float(item.get("end", start_time)), start_time)
            normalized.append(RawChunk(text=text, start_time=start_time, end_time=end_time))
        return normalized

    def _build_sentence_units(self, chunks: list[RawChunk]) -> list[SentenceUnit]:
        if not chunks:
            return []

        sentences: list[SentenceUnit] = []
        buffer = ""
        buffer_start = chunks[0].start_time
        buffer_end = chunks[0].end_time

        for chunk in chunks:
            text = self._clean_fragment(chunk.text)
            if not text:
                continue

            if not buffer:
                buffer_start = chunk.start_time

            buffer = f"{buffer} {text}".strip()
            buffer_end = chunk.end_time

            parts = SENTENCE_SPLIT_PATTERN.split(buffer)
            trailing_fragment = ""
            if parts and not re.search(r"[.!?]$", buffer):
                trailing_fragment = parts.pop() if parts else buffer

            for part in parts:
                normalized = self._normalize_sentence(part)
                if not self._is_valid_sentence(normalized):
                    trailing_fragment = f"{normalized} {trailing_fragment}".strip()
                    continue
                sentences.append(
                    SentenceUnit(
                        text=normalized,
                        start_time=buffer_start,
                        end_time=chunk.end_time,
                        sequence=len(sentences) + 1,
                    )
                )
                buffer_start = chunk.end_time

            buffer = trailing_fragment.strip()

        if buffer:
            fallback_parts = re.split(r"(?<=,)\s+|(?<=;)\s+", buffer)
            current_start = buffer_start
            for part in fallback_parts:
                normalized = self._normalize_sentence(part)
                if not self._is_valid_sentence(normalized):
                    continue
                sentences.append(
                    SentenceUnit(
                        text=normalized,
                        start_time=current_start,
                        end_time=buffer_end,
                        sequence=len(sentences) + 1,
                    )
                )
                current_start = buffer_end

        if not sentences:
            combined = self._normalize_sentence(" ".join(chunk.text for chunk in chunks))
            if combined:
                sentences.append(
                    SentenceUnit(
                        text=combined,
                        start_time=chunks[0].start_time,
                        end_time=chunks[-1].end_time,
                        sequence=1,
                    )
                )

        return self._merge_short_sentences(sentences)

    def _merge_short_sentences(self, sentences: list[SentenceUnit]) -> list[SentenceUnit]:
        if not sentences:
            return []

        merged: list[SentenceUnit] = []
        for sentence in sentences:
            word_count = len(WORD_PATTERN.findall(sentence.text))
            if merged and word_count < 8:
                previous = merged[-1]
                previous.text = self._normalize_sentence(f"{previous.text} {sentence.text}")
                previous.end_time = sentence.end_time
                continue
            merged.append(sentence)

        for index, sentence in enumerate(merged, start=1):
            sentence.sequence = index

        return [sentence for sentence in merged if self._is_valid_sentence(sentence.text)]

    def _build_topic_units(self, sentences: list[SentenceUnit]) -> list[TopicUnit]:
        if not sentences:
            return []

        groups = self._group_sentences(sentences)
        topics: list[TopicUnit] = []
        for index, group in enumerate(groups, start=1):
            text = " ".join(sentence.text for sentence in group).strip()
            title = self._infer_topic_title(text, index)
            description = self._build_description(group)
            keywords = self._extract_keywords(text)
            topics.append(
                TopicUnit(
                    sequence=index,
                    title=title,
                    description=description,
                    keywords=keywords,
                    content=text,
                    sentences=group,
                )
            )
        return topics

    def _build_topic_units_with_llm(self, sentences: list[SentenceUnit]) -> list[TopicUnit]:
        if not sentences or not self.openrouter.is_configured:
            return []

        try:
            plans = self.openrouter.group_topics([sentence.text for sentence in sentences])
        except Exception:
            return []

        if not plans:
            return []

        sentence_lookup = {index: sentence for index, sentence in enumerate(sentences, start=1)}
        seen_indexes: set[int] = set()
        topics: list[TopicUnit] = []

        for sequence, plan in enumerate(plans, start=1):
            valid_indexes = [index for index in plan.sentence_indexes if index in sentence_lookup and index not in seen_indexes]
            if not valid_indexes:
                continue
            group = [sentence_lookup[index] for index in valid_indexes]
            seen_indexes.update(valid_indexes)
            content = " ".join(sentence.text for sentence in group).strip()
            title = plan.topic.strip() or self._infer_topic_title(content, sequence)
            description = plan.description.strip() or self._build_description(group)
            keywords = [keyword for keyword in plan.keywords if keyword][:5] or self._extract_keywords(content)
            topics.append(
                TopicUnit(
                    sequence=sequence,
                    title=title,
                    description=description,
                    keywords=keywords,
                    content=content,
                    sentences=group,
                )
            )

        remaining = [sentence for index, sentence in sentence_lookup.items() if index not in seen_indexes]
        if remaining:
            if topics:
                last_topic = topics[-1]
                last_topic.sentences.extend(remaining)
                last_topic.content = " ".join(sentence.text for sentence in last_topic.sentences).strip()
                last_topic.description = self._build_description(last_topic.sentences)
                last_topic.keywords = self._extract_keywords(last_topic.content)
            else:
                return []

        for index, topic in enumerate(topics, start=1):
            topic.sequence = index

        return topics

    def _group_sentences(self, sentences: list[SentenceUnit]) -> list[list[SentenceUnit]]:
        if len(sentences) <= 4:
            return [sentences]

        boundary_scores: list[tuple[float, int]] = []
        for index in range(1, len(sentences)):
            boundary_scores.append((self._boundary_score(sentences, index), index))

        candidate_boundaries = [
            index
            for score, index in sorted(boundary_scores, key=lambda item: item[0], reverse=True)
            if score >= 1.1
        ]

        boundaries: list[int] = []
        min_topic_span = 3
        for boundary in candidate_boundaries:
            if any(abs(boundary - existing) < min_topic_span for existing in boundaries):
                continue
            boundaries.append(boundary)

        boundaries.sort()
        groups: list[list[SentenceUnit]] = []
        start = 0
        for boundary in boundaries:
            group = sentences[start:boundary]
            if group:
                groups.append(group)
            start = boundary
        groups.append(sentences[start:])

        return self._merge_small_groups(groups)

    def _merge_small_groups(self, groups: list[list[SentenceUnit]]) -> list[list[SentenceUnit]]:
        merged: list[list[SentenceUnit]] = []
        for group in groups:
            if not group:
                continue
            is_small = len(group) < 3 or sum(len(WORD_PATTERN.findall(sentence.text)) for sentence in group) < 24
            if is_small and merged:
                merged[-1].extend(group)
            else:
                merged.append(group)

        if len(merged) > 1 and len(merged[-1]) < 3:
            merged[-2].extend(merged[-1])
            merged.pop()

        return merged

    def _boundary_score(self, sentences: list[SentenceUnit], index: int) -> float:
        current = sentences[index].text.lower()
        previous_window = " ".join(sentence.text for sentence in sentences[max(0, index - 2):index])
        next_window = " ".join(sentence.text for sentence in sentences[index:min(len(sentences), index + 2)])
        previous_keywords = set(self._extract_keywords(previous_window))
        next_keywords = set(self._extract_keywords(next_window))
        overlap = len(previous_keywords & next_keywords) / max(1, len(previous_keywords | next_keywords))

        time_gap = max(0.0, sentences[index].start_time - sentences[index - 1].end_time)
        score = 0.0
        if any(current.startswith(marker) for marker in BOUNDARY_MARKERS):
            score += 0.9
        if overlap <= 0.2:
            score += 0.6
        elif overlap <= 0.35:
            score += 0.3
        if time_gap >= 2.5:
            score += 0.4
        if time_gap >= 6.0:
            score += 0.3
        return score

    def _infer_topic_title(self, text: str, sequence: int) -> str:
        lowered = text.lower()
        patterns = [
            r"(?:we(?: are|'re| will|'ll)?\s+(?:discuss|cover|study|focus on|talk about|look at)\s+)(.+?)(?:[,.]|$)",
            r"(?:this topic(?: is about| covers)\s+)(.+?)(?:[,.]|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                title = self._title_case_phrase(match.group(1))
                if title:
                    return title

        keywords = self._extract_keywords(text)
        if keywords:
            return " ".join(word.title() for word in keywords[:4])
        return f"Topic {sequence}"

    def _build_description(self, sentences: list[SentenceUnit]) -> str:
        description = " ".join(sentence.text for sentence in sentences[:3]).strip()
        return description[:700]

    def _extract_keywords(self, text: str) -> list[str]:
        counts = Counter(
            token.lower()
            for token in TOKEN_PATTERN.findall(text)
            if token.lower() not in STOPWORDS
        )
        return [token for token, _count in counts.most_common(5)]

    def _clean_fragment(self, text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip()
        value = re.sub(r"\b(\w+)( \1\b)+", r"\1", value, flags=re.IGNORECASE)
        for filler in FILLER_PATTERNS:
            value = re.sub(rf"^(?:{re.escape(filler)})[\s,.:;-]*", "", value, flags=re.IGNORECASE)
        return value.strip(" ,;-")

    def _normalize_sentence(self, text: str) -> str:
        value = self._clean_fragment(text)
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return ""
        value = value[0].upper() + value[1:]
        if value[-1] not in ".!?":
            value += "."
        return value

    def _is_valid_sentence(self, text: str) -> bool:
        words = WORD_PATTERN.findall(text)
        if len(words) < 8:
            return False
        lowered = text.lower()
        if any(lowered == filler or lowered.startswith(f"{filler}.") for filler in FILLER_PATTERNS):
            return False
        if len(set(word.lower() for word in words)) <= 3:
            return False
        return True

    def _title_case_phrase(self, text: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9\s-]", " ", text)
        words = [word for word in cleaned.split() if word.lower() not in STOPWORDS]
        return " ".join(word.title() for word in words[:6])
