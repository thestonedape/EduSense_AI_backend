from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from app.core.config import get_settings


settings = get_settings()
logger = logging.getLogger("app.openrouter")


@dataclass
class TopicPlan:
    topic: str
    description: str
    keywords: list[str]
    sentence_indexes: list[int]


@dataclass
class ClaimAssessment:
    verdict: str
    confidence: float
    rationale: str


class OpenRouterService:
    def __init__(self) -> None:
        self.api_key = settings.openrouter_api_key
        self.api_url = settings.openrouter_api_url
        self.model = settings.openrouter_model
        self.site_url = settings.openrouter_site_url
        self.app_name = settings.openrouter_app_name
        self.timeout = max(int(settings.openrouter_timeout_seconds), 5)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    def group_topics(self, sentences: list[str]) -> list[TopicPlan]:
        if not self.is_configured or not sentences:
            return []

        indexed_sentences = "\n".join(f"{index}. {sentence}" for index, sentence in enumerate(sentences, start=1))
        system_prompt = (
            "You organize college lecture transcripts into semantic topics. "
            "Preserve the professor's meaning. Do not invent facts. "
            "Return JSON only."
        )
        user_prompt = (
            "Group the numbered lecture sentences into the smallest set of semantically coherent topics.\n"
            "Rules:\n"
            "- Do not force a fixed number of topics.\n"
            "- Prefer fewer, stronger topics.\n"
            "- Every sentence index must belong to exactly one topic.\n"
            "- No overlapping sentence indexes.\n"
            "- Topic names must be concise and academic.\n"
            "- Description should be 1-2 sentences.\n"
            "- Keywords should contain 3 to 5 meaningful technical terms.\n\n"
            "Return this JSON shape:\n"
            "{\n"
            '  "topics": [\n'
            '    {"topic": "", "description": "", "keywords": ["", ""], "sentence_indexes": [1, 2]}\n'
            "  ]\n"
            "}\n\n"
            f"Sentences:\n{indexed_sentences}"
        )
        payload = self._post_json("topic_grouping", system_prompt, user_prompt)
        topics = payload.get("topics", [])
        plans: list[TopicPlan] = []
        for item in topics:
            if not isinstance(item, dict):
                continue
            indexes = [int(value) for value in item.get("sentence_indexes", []) if isinstance(value, int)]
            topic = str(item.get("topic", "")).strip()
            description = str(item.get("description", "")).strip()
            keywords = [str(value).strip() for value in item.get("keywords", []) if str(value).strip()]
            if topic and indexes:
                plans.append(
                    TopicPlan(
                        topic=topic,
                        description=description,
                        keywords=keywords[:5],
                        sentence_indexes=indexes,
                    )
                )
        return plans

    def extract_flagged_claims(self, statements: list[str], max_claims: int = 8) -> list[str]:
        if not self.is_configured or not statements:
            return []

        indexed_statements = "\n".join(f"{index}. {sentence}" for index, sentence in enumerate(statements, start=1))
        system_prompt = (
            "You review lecture statements and select only factual assertions that are worth fact checking. "
            "Ignore filler, explanations, examples, and conversational lines. Return JSON only."
        )
        user_prompt = (
            "From the numbered lecture statements below, return only statements that are factual, checkable, "
            "important enough to verify, and potentially error-prone. Prefer fewer items. "
            f"Return at most {max_claims} items.\n\n"
            "Return this JSON shape:\n"
            '{ "claims": [{"statement_index": 1, "claim": ""}] }\n\n'
            f"Statements:\n{indexed_statements}"
        )
        payload = self._post_json("claim_extraction", system_prompt, user_prompt)
        claims = payload.get("claims", [])
        selected: list[str] = []
        for item in claims:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim", "")).strip()
            if claim:
                selected.append(claim)
        return selected[:max_claims]

    def assess_claim(
        self,
        claim_text: str,
        evidence_items: list[str],
        *,
        subject_context: str | None = None,
        use_model_knowledge: bool = False,
    ) -> ClaimAssessment | None:
        if not self.is_configured:
            return None

        evidence_blob = "\n\n".join(f"Evidence {index}:\n{item}" for index, item in enumerate(evidence_items, start=1))
        subject_line = f"Subject context: {subject_context}\n\n" if subject_context else ""
        if use_model_knowledge or not evidence_items:
            system_prompt = (
                "You are evaluating whether a lecture claim is academically correct using your general subject knowledge. "
                "Be conservative. If you are not clearly confident, mark it uncertain. Return JSON only."
            )
            user_prompt = (
                "Evaluate this claim using established academic knowledge.\n"
                "Allowed verdicts: true, false, uncertain.\n"
                "Return this JSON shape:\n"
                '{ "verdict": "uncertain", "confidence": 0.5, "rationale": "" }\n\n'
                f"{subject_line}"
                f"Claim:\n{claim_text}"
            )
        else:
            system_prompt = (
                "You are evaluating whether a lecture claim is supported by retrieved reference evidence. "
                "Be conservative. If the evidence does not clearly support or contradict the claim, mark it uncertain. "
                "Return JSON only."
            )
            user_prompt = (
                "Evaluate this claim against the evidence.\n"
                "Allowed verdicts: true, false, uncertain.\n"
                "Return this JSON shape:\n"
                '{ "verdict": "uncertain", "confidence": 0.5, "rationale": "" }\n\n'
                f"{subject_line}"
                f"Claim:\n{claim_text}\n\n"
                f"Evidence:\n{evidence_blob}"
            )
        payload = self._post_json("claim_assessment", system_prompt, user_prompt)
        verdict = str(payload.get("verdict", "uncertain")).strip().lower()
        if verdict not in {"true", "false", "uncertain"}:
            verdict = "uncertain"
        confidence = payload.get("confidence", 0.5)
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError):
            numeric_confidence = 0.5
        numeric_confidence = min(max(numeric_confidence, 0.0), 1.0)
        rationale = str(payload.get("rationale", "")).strip() or "OpenRouter returned no rationale."
        return ClaimAssessment(verdict=verdict, confidence=numeric_confidence, rationale=rationale)

    def answer_student_question(
        self,
        *,
        question: str,
        lecture_title: str,
        subject_context: str | None,
        context_items: list[dict[str, str]],
    ) -> str | None:
        if not self.is_configured or not context_items:
            return None

        context_blob = "\n\n".join(
            f"Topic: {item.get('topic', 'General')}\n"
            f"Source: {item.get('source', 'Lecture')}\n"
            f"Content: {item.get('content', '')}"
            for item in context_items
        )
        subject_line = f"Subject context: {subject_context}\n" if subject_context else ""
        system_prompt = (
            "You are a careful student tutor. Answer only from the validated lecture and reference context provided. "
            "Do not invent facts outside the supplied context. Keep the answer concise, clear, and study-friendly. "
            "Return JSON only."
        )
        user_prompt = (
            f"Lecture title: {lecture_title}\n"
            f"{subject_line}"
            "Return this JSON shape:\n"
            '{ "answer": "" }\n\n'
            f"Question:\n{question}\n\n"
            f"Validated context:\n{context_blob}"
        )
        payload = self._post_json("student_answer", system_prompt, user_prompt)
        answer = str(payload.get("answer", "")).strip()
        return answer or None

    def answer_student_doubt(
        self,
        *,
        question: str,
        subject_context: str | None,
        context_items: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        if not self.is_configured or not context_items:
            return None

        context_blob = "\n\n".join(
            f"Topic: {item.get('topic', 'General')}\n"
            f"Source: {item.get('source', 'Lecture')}\n"
            f"Content: {item.get('content', '')}"
            for item in context_items
        )
        subject_line = f"Subject scope: {subject_context}\n" if subject_context else ""
        system_prompt = (
            "You are a careful academic tutor inside a student doubt-solving workspace. "
            "Answer only from the approved learning context provided. "
            "Write like a strong study assistant, not like a rigid template generator. "
            "If the question needs a direct answer, give a direct answer. "
            "If the question needs steps, comparison, intuition, or structure, use that naturally. "
            "Do not force the same section layout for every question. "
            "Keep the answer natural, readable, and grounded in the provided context. "
            "Return JSON only."
        )
        user_prompt = (
            f"{subject_line}"
            "Return this JSON shape:\n"
            '{'
            ' "answer": "",'
            ' "structured_answer": {'
            '   "core_concept": "",'
            '   "simple_explanation": "",'
            '   "deep_explanation": "",'
            '   "example_or_analogy": "",'
            '   "key_takeaways": [""]'
            " }"
            " }\n\n"
            "Rules:\n"
            "- Put the main student-facing answer in `answer`.\n"
            "- Default to a natural direct answer in `answer`.\n"
            "- Use `structured_answer` only if the student explicitly asks for notes, takeaways, summary, points, or exam-style structure.\n"
            "- Leave unused `structured_answer` fields null or empty.\n"
            "- `key_takeaways` must be an array of short strings when used.\n"
            "- Stay grounded in the supplied context only.\n\n"
            "Few-shot guidance:\n"
            '1. Question: "what is svm"\n'
            '   Good style: direct concept explanation in natural prose, maybe 1-2 short paragraphs.\n'
            '2. Question: "derive svm"\n'
            '   Good style: stepwise reasoning with equations or optimization intuition if available.\n'
            '3. Question: "svm vs logistic regression"\n'
            '   Good style: comparison with contrasts, not the standard concept template.\n'
            '4. Question: "give exam answer for svm"\n'
            '   Good style: compact exam-ready points.\n\n'
            f"Question:\n{question}\n\n"
            f"Approved context:\n{context_blob}"
        )
        payload = self._post_json("student_doubt_answer", system_prompt, user_prompt)
        answer = str(payload.get("answer", "")).strip()
        structured = payload.get("structured_answer")
        if not answer and not isinstance(structured, dict):
            return None
        return {
            "answer": answer,
            "structured_answer": structured if isinstance(structured, dict) else None,
        }

    def generate_practice_questions(self, lecture_items: list[dict[str, object]], limit: int = 6) -> list[dict]:
        if not self.is_configured or not lecture_items:
            return []

        lecture_blob = json.dumps(lecture_items, ensure_ascii=True)
        system_prompt = (
            "You generate revision-friendly multiple choice questions from validated lecture content. "
            "Keep them accurate, simple, and grounded in the supplied material. Return JSON only."
        )
        user_prompt = (
            f"Generate up to {limit} multiple choice questions.\n"
            "Rules:\n"
            "- Each question must have exactly 4 options.\n"
            "- The correct answer index must be 0-3.\n"
            "- Keep explanations short and helpful.\n\n"
            "Return this JSON shape:\n"
            '{ "questions": [{"lecture_name": "", "question": "", "options": ["", "", "", ""], "answer": 0, "explanation": ""}] }\n\n'
            f"Validated lecture content:\n{lecture_blob}"
        )
        payload = self._post_json("practice_generation", system_prompt, user_prompt)
        questions = payload.get("questions", [])
        return questions if isinstance(questions, list) else []

    def _post_json(self, operation: str, system_prompt: str, user_prompt: str) -> dict:
        started_at = time.perf_counter()
        logger.info(
            "openrouter_request_start operation=%s model=%s prompt_chars=%s configured=%s",
            operation,
            self.model,
            len(system_prompt) + len(user_prompt),
            self.is_configured,
        )
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self.site_url,
                    "X-Title": self.app_name,
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.exception(
                "openrouter_request_failed operation=%s model=%s elapsed_ms=%s",
                operation,
                self.model,
                elapsed_ms,
            )
            raise

        usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "openrouter_request_success operation=%s model=%s elapsed_ms=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            operation,
            self.model,
            elapsed_ms,
            usage.get("prompt_tokens", "n/a"),
            usage.get("completion_tokens", "n/a"),
            usage.get("total_tokens", "n/a"),
        )
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            text = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        else:
            text = str(content)
        return json.loads(text)
