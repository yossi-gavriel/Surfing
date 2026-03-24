from dataclasses import dataclass
from typing import Any

import numpy as np

from shared.utils.logger import get_logger
from shared.utils.match_decision import (
    CandidateScore,
    MatchDecision,
    evaluate_match_decision,
    evaluate_track_match,
)
from shared.utils.system_config import SystemConfigService
from src.config import MatchingConfig
from src.db import UsersDB, normalize_embeddings

logger = get_logger("matcher")


@dataclass
class MatchResult:
    user_id: str
    pool_id: str | None
    track_id: str
    camera_id: str | None
    video_id: str | None
    source_video_s3: str | None
    timestamp: str | None
    keyframe: str | None
    keyframe_s3: str | None
    score: float
    confidence: float
    min_distance: float
    mean_distance: float
    max_distance: float
    distance_std: float
    embeddings_used: int
    track_evidence_count: int
    payload_consistency: float | None
    second_best_score: float | None
    score_margin: float | None
    force_match_used: bool
    threshold_used: float
    margin_threshold_used: float
    decision_reason: str | None
    decision_explanation: str


@dataclass
class MatchAttempt:
    match_result: MatchResult | None
    decision: MatchDecision
    track_id: str | None
    best_user_id: str | None


class ExactTrackEmbeddingSearchEngine:
    def search(
        self,
        *,
        track_embedding: np.ndarray,
        users: list[dict[str, Any]],
        score_candidate,
    ) -> list[CandidateScore]:
        ranked_scores = [score_candidate(user=user) for user in users]
        ranked_scores.sort(
            key=lambda candidate: (
                candidate.best_similarity,
                candidate.final_score,
                -candidate.aggregated_distance,
            ),
            reverse=True,
        )
        return ranked_scores


class Matcher:
    def __init__(
        self,
        users_db: UsersDB,
        config: MatchingConfig,
        system_config: SystemConfigService | None = None,
        *,
        search_backend: ExactTrackEmbeddingSearchEngine | None = None,
    ):
        self.users_db = users_db
        self.config = config
        self.system_config = system_config
        self.search_engine = search_backend or ExactTrackEmbeddingSearchEngine()

    def match(
        self,
        payload: dict[str, Any],
        *,
        candidate_users: list[dict[str, Any]] | None = None,
    ) -> MatchResult | None:
        return self.evaluate_match(payload, candidate_users=candidate_users).match_result

    def evaluate_match(
        self,
        payload: dict[str, Any],
        *,
        candidate_users: list[dict[str, Any]] | None = None,
    ) -> MatchAttempt:
        users = candidate_users or self.users_db.get_all_users(pool_id=payload.get("pool_id"))
        if not users:
            logger.warning("No users available for matching")
            return MatchAttempt(
                match_result=None,
                decision=evaluate_match_decision(
                    best_similarity=None,
                    second_best_similarity=None,
                    similarity_threshold=self._min_similarity(),
                    margin_threshold=self._min_margin(),
                ),
                track_id=payload.get("track_id"),
                best_user_id=None,
            )

        track_embedding = self._extract_track_embedding(payload)
        evidence_count = self._get_track_evidence_count(payload)
        payload_consistency = self._get_payload_consistency(payload)
        quality_avg = self._get_quality_avg(payload)
        validation_error = self._get_track_validation_error(
            evidence_count=evidence_count,
            payload_consistency=payload_consistency,
        )
        if validation_error is not None:
            raise ValueError(validation_error)

        logger.info(
            "Matching start track_id=%s pool_id=%s candidates=%s evidence_count=%s quality_avg=%s consistency=%s",
            payload.get("track_id"),
            payload.get("pool_id"),
            len(users),
            evidence_count,
            "n/a" if quality_avg is None else f"{quality_avg:.4f}",
            "n/a" if payload_consistency is None else f"{payload_consistency:.4f}",
        )

        evaluation = evaluate_track_match(
            track_embedding=track_embedding,
            users=users,
            similarity_threshold=self._min_similarity(),
            margin_threshold=self._min_margin(),
            min_track_embeddings=self._min_track_embeddings(),
            min_track_consistency=self.config.min_track_consistency,
            evidence_count=evidence_count,
            payload_consistency=payload_consistency,
            quality_avg=quality_avg,
        )
        ranked_candidates = evaluation.ranked_candidates
        if not ranked_candidates:
            return MatchAttempt(
                match_result=None,
                decision=evaluation.decision,
                track_id=payload.get("track_id"),
                best_user_id=None,
            )

        best_candidate = evaluation.best_candidate
        second_best_candidate = evaluation.second_best_candidate
        assert best_candidate is not None
        decision = evaluation.decision

        logger.info(
            "Decision for track_id=%s user_id=%s best_similarity=%s second_best_similarity=%s margin=%s threshold_used=%.4f margin_threshold_used=%.4f decision=%s reason=%s",
            payload.get("track_id"),
            best_candidate.user_id,
            "n/a" if decision.best_similarity is None else f"{decision.best_similarity:.4f}",
            "n/a" if second_best_candidate is None else f"{second_best_candidate.best_similarity:.4f}",
            "n/a" if decision.margin is None else f"{decision.margin:.4f}",
            decision.threshold_used,
            decision.margin_threshold_used,
            "accepted" if decision.final_verdict == "match" else f"rejected ({decision.decision_reason})",
            decision.decision_reason or "accepted",
        )

        if decision.final_verdict != "match":
            self._print_rejected_match(
                payload=payload,
                candidate=best_candidate,
                decision=decision,
            )
            return MatchAttempt(
                match_result=None,
                decision=decision,
                track_id=payload.get("track_id"),
                best_user_id=best_candidate.user_id,
            )

        match_result = self._build_match_result(
            payload=payload,
            best_candidate=best_candidate,
            second_best_candidate=second_best_candidate,
            evidence_count=evidence_count,
            payload_consistency=payload_consistency,
            force_match_used=False,
            decision=decision,
        )
        return MatchAttempt(
            match_result=match_result,
            decision=decision,
            track_id=payload.get("track_id"),
            best_user_id=best_candidate.user_id,
        )

    def _extract_track_embedding(self, payload: dict[str, Any]) -> np.ndarray:
        raw_embedding = (
            payload.get("track_embedding")
            or payload.get("face_embedding")
            or payload.get("embedding")
            or payload.get("embeddings")
        )
        normalized = normalize_embeddings(raw_embedding)
        if normalized.size == 0:
            raise ValueError("empty embedding payload")
        if len(normalized) == 1:
            return normalized[0]

        aggregated = np.mean(normalized, axis=0)
        normalized_aggregated = normalize_embeddings(aggregated)
        if normalized_aggregated.size == 0:
            raise ValueError("track embedding could not be normalized")
        return normalized_aggregated[0]

    def _get_track_evidence_count(self, payload: dict[str, Any]) -> int:
        for key in ("frames_count", "num_faces_detected", "num_embeddings"):
            raw_value = payload.get(key)
            if raw_value is None:
                continue
            try:
                return max(int(raw_value), 0)
            except (TypeError, ValueError):
                continue
        return 0

    def _get_payload_consistency(self, payload: dict[str, Any]) -> float | None:
        raw_value = payload.get("consistency")
        if raw_value is None:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    def _get_quality_avg(self, payload: dict[str, Any]) -> float | None:
        raw_value = payload.get("quality_avg")
        if raw_value is None:
            raw_value = payload.get("avg_quality")
        if raw_value is None:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    def _get_track_validation_error(
        self,
        *,
        evidence_count: int,
        payload_consistency: float | None,
    ) -> str | None:
        runtime_min_frames = self._min_track_embeddings()
        if evidence_count < runtime_min_frames:
            return "track has fewer than minimum supported frames"
        if payload_consistency is not None and payload_consistency < self.config.min_track_consistency:
            return "track consistency below minimum threshold"
        return None

    def _print_rejected_match(
        self,
        *,
        payload: dict[str, Any],
        candidate: CandidateScore,
        decision: MatchDecision,
    ) -> None:
        print(
            {
                "video_embedding_id": payload.get("video_embedding_id") or payload.get("track_id"),
                "user_embedding_id": candidate.best_user_embedding_id,
                "distance": candidate.aggregated_distance,
                "similarity": candidate.best_similarity,
                "second_best_similarity": decision.second_best_similarity,
                "margin": decision.margin,
                "threshold": decision.threshold_used,
                "margin_threshold": decision.margin_threshold_used,
                "rejection_reason": decision.decision_reason,
                "explanation": decision.explanation,
                "decision": "rejected",
            }
        )

    def _should_force_match(self, candidate: CandidateScore) -> bool:
        return False

    def _build_match_result(
        self,
        *,
        payload: dict[str, Any],
        best_candidate: CandidateScore,
        second_best_candidate: CandidateScore | None,
        evidence_count: int,
        payload_consistency: float | None,
        force_match_used: bool,
        decision: MatchDecision,
    ) -> MatchResult:
        confidence = max(0.0, min(1.0, 1.0 - best_candidate.aggregated_distance))
        return MatchResult(
            user_id=best_candidate.user_id,
            pool_id=payload.get("pool_id"),
            track_id=str(payload["track_id"]),
            camera_id=payload.get("camera_id"),
            video_id=self._get_video_id(payload),
            source_video_s3=payload.get("source_video_s3"),
            timestamp=self._get_timestamp(payload),
            keyframe=self._get_keyframe(payload),
            keyframe_s3=payload.get("keyframe_s3") or payload.get("keyframe"),
            score=best_candidate.best_similarity,
            confidence=confidence,
            min_distance=best_candidate.aggregated_distance,
            mean_distance=best_candidate.mean_distance,
            max_distance=best_candidate.max_distance,
            distance_std=best_candidate.distance_std,
            embeddings_used=1,
            track_evidence_count=evidence_count,
            payload_consistency=payload_consistency,
            second_best_score=None if second_best_candidate is None else second_best_candidate.best_similarity,
            score_margin=decision.margin,
            force_match_used=force_match_used,
            threshold_used=decision.threshold_used,
            margin_threshold_used=decision.margin_threshold_used,
            decision_reason=decision.decision_reason,
            decision_explanation=decision.explanation,
        )

    def _get_video_id(self, payload: dict[str, Any]) -> str | None:
        return payload.get("video_id") or payload.get("source_video_id")

    def _get_timestamp(self, payload: dict[str, Any]) -> str | None:
        return payload.get("timestamp") or payload.get("start_time")

    def _get_keyframe(self, payload: dict[str, Any]) -> str | None:
        return payload.get("keyframe") or payload.get("keyframe_s3")

    def _min_similarity(self) -> float:
        if self.system_config is None:
            return float(self.config.min_similarity)
        return float(
            self.system_config.get_config(
                "min_similarity",
                self.config.min_similarity,
            )
        )

    def _min_margin(self) -> float:
        if self.system_config is None:
            return float(self.config.min_margin)
        return float(
            self.system_config.get_config(
                "min_margin",
                self.config.min_margin,
            )
        )

    def _min_track_embeddings(self) -> int:
        if self.system_config is None:
            return int(self.config.min_track_embeddings)
        return int(
            self.system_config.get_config(
                "min_frames_per_track",
                self.config.min_track_embeddings,
            )
        )
