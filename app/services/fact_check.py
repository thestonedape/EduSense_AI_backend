import logging
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.claim import Claim, ClaimEvidence, ClaimStatus, ClaimVerdict
from app.models.knowledge import KnowledgeChunk
from app.models.lecture import Lecture
from app.models.reference_file import ReferenceFile
from app.models.transcript import TranscriptSegment
from app.services.embedding import EmbeddingService
from app.services.openrouter import OpenRouterService


CLAIM_PATTERN = re.compile(r".{20,}")
logger = logging.getLogger("app.fact_check")


@dataclass
class FactCheckRunSummary:
    candidate_count: int
    false_claim_count: int
    validation_source: str


class FactCheckService:
    def __init__(self) -> None:
        self.embedding_service = EmbeddingService()
        self.openrouter = OpenRouterService()

    def extract_claim_candidates(self, transcript_segments: list[TranscriptSegment]) -> list[str]:
        if self.openrouter.is_configured:
            try:
                statements = [
                    (segment.edited_text or segment.text).strip()
                    for segment in transcript_segments
                    if (segment.edited_text or segment.text).strip()
                ]
                selected = self.openrouter.extract_flagged_claims(statements, max_claims=8)
                if selected:
                    return selected
            except Exception:
                pass

        candidates: list[str] = []
        for segment in transcript_segments:
            text = (segment.edited_text or segment.text).strip()
            if not CLAIM_PATTERN.match(text):
                continue
            if any(token in text.lower() for token in [" is ", " are ", " always ", " never ", " all ", " must ", " can "]):
                candidates.append(text)
        return candidates[:8]

    async def generate_claims(
        self,
        session: AsyncSession,
        lecture_id,
        transcript_segments: list[TranscriptSegment],
    ) -> tuple[list[Claim], FactCheckRunSummary]:
        await session.execute(delete(Claim).where(Claim.lecture_id == lecture_id))
        lecture = await session.scalar(select(Lecture).where(Lecture.id == lecture_id))
        reference_count = await session.scalar(
            select(func.count()).select_from(ReferenceFile).where(ReferenceFile.lecture_id == lecture_id)
        )
        has_reference_material = bool(reference_count)
        candidates = self.extract_claim_candidates(transcript_segments)
        claims: list[Claim] = []
        validation_source = "reference_evidence" if has_reference_material else "model_knowledge"

        sequence = 1
        for claim_text in candidates:
            evidence = await self.retrieve_evidence(session, lecture_id, claim_text) if has_reference_material else []
            verdict, confidence, rationale = self.score_claim(
                claim_text,
                evidence,
                subject_context=getattr(lecture, "subject_name", None) or getattr(lecture, "subject_code", None),
                use_model_knowledge=not has_reference_material,
            )

            if self.openrouter.is_configured and verdict != ClaimVerdict.false:
                # Only raise claims that OpenRouter marks as false.
                continue

            claim = Claim(
                lecture_id=lecture_id,
                sequence=sequence,
                text=claim_text,
                verdict=verdict,
                confidence=confidence,
                status=ClaimStatus.pending,
                source_excerpt=evidence[0].content if evidence else None,
                rationale=rationale,
                details={
                    "evidence_count": len(evidence),
                    "validation_source": validation_source,
                },
            )
            session.add(claim)
            await session.flush()

            for item in evidence:
                session.add(
                    ClaimEvidence(
                        claim_id=claim.id,
                        source_type=item.details.get("kind", "knowledge"),
                        source_reference=str(item.lecture_id),
                        excerpt=item.content,
                        similarity_score=self.similarity_score(claim_text, item.content),
                    )
                )
            claims.append(claim)
            sequence += 1

        logger.info(
            "fact_check_completed lecture=%s candidate_count=%s stored_false_claims=%s validation_source=%s",
            lecture_id,
            len(candidates),
            len(claims),
            validation_source,
        )

        return claims, FactCheckRunSummary(
            candidate_count=len(candidates),
            false_claim_count=len(claims),
            validation_source=validation_source,
        )

    async def retrieve_evidence(self, session: AsyncSession, lecture_id, claim_text: str, limit: int = 4) -> list[KnowledgeChunk]:
        embedding = self.embedding_service.encode([claim_text])[0]
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.lecture_id == lecture_id)
            .order_by(KnowledgeChunk.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        result = list(await session.scalars(stmt))
        reference_items = [item for item in result if str(item.details.get("kind", "")).startswith("reference_")]
        full_lecture_items = [item for item in result if item.details.get("kind") == "lecture_full"]
        support_items = [
            item for item in result
            if item not in reference_items and item.details.get("kind") != "lecture_full"
        ]
        prioritized = reference_items[:2] + full_lecture_items[:1] + support_items
        return prioritized[:limit]

    def similarity_score(self, claim_text: str, evidence_text: str) -> float:
        claim_tokens = set(re.findall(r"\w+", claim_text.lower()))
        evidence_tokens = set(re.findall(r"\w+", evidence_text.lower()))
        if not claim_tokens or not evidence_tokens:
            return 0.0
        overlap = len(claim_tokens & evidence_tokens) / len(claim_tokens)
        return round(min(max(overlap, 0.0), 1.0), 2)

    def score_claim(
        self,
        claim_text: str,
        evidence: list[KnowledgeChunk],
        *,
        subject_context: str | None = None,
        use_model_knowledge: bool = False,
    ) -> tuple[ClaimVerdict, float, str]:
        if self.openrouter.is_configured:
            try:
                assessment = self.openrouter.assess_claim(
                    claim_text,
                    [item.content for item in evidence],
                    subject_context=subject_context,
                    use_model_knowledge=use_model_knowledge,
                )
                if assessment is not None:
                    return ClaimVerdict(assessment.verdict), assessment.confidence, assessment.rationale
            except Exception:
                pass

        if not evidence:
            return ClaimVerdict.uncertain, 0.35, "No supporting evidence was retrieved from the knowledge base."

        best_score = max(self.similarity_score(claim_text, item.content) for item in evidence)
        if best_score >= 0.65:
            return ClaimVerdict.true, min(0.95, 0.65 + best_score / 3), "Top evidence strongly overlaps with the extracted claim."
        if best_score >= 0.4:
            return ClaimVerdict.uncertain, 0.55, "Evidence is related but not decisive enough for automatic approval."
        return ClaimVerdict.false, 0.68, "Retrieved evidence diverges materially from the extracted claim."

    async def get_claims_for_lecture(self, session: AsyncSession, lecture_id):
        stmt = (
            select(Claim)
            .where(Claim.lecture_id == lecture_id)
            .options(selectinload(Claim.evidence_items))
            .order_by(Claim.sequence)
        )
        result = await session.scalars(stmt)
        return list(result)
