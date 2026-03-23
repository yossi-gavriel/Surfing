from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.spatial.distance import cdist

from shared.utils.logger import get_logger
from src.config import MatchingConfig
from src.db import UsersDB, normalize_embeddings

logger = get_logger("matcher")


@dataclass
class MatchResult:
    user_id: str
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


@dataclass
class CandidateScore:
    user_id: str
    aggregated_distance: float
    mean_distance: float
    max_distance: float
    distance_std: float
    per_embedding_distances: list[float]
    final_score: float
    passes_consistency: bool


class Matcher:
    def __init__(self, users_db: UsersDB, config: MatchingConfig):
        self.users_db = users_db
        self.config = config

    def match(self, payload: dict[str, Any]) -> MatchResult | None:
        users = self.users_db.get_all_users()
        if not users:
            logger.warning("No users available for matching")
            return None

        track_embeddings = self._extract_track_embeddings(payload)
        if track_embeddings.size == 0:
            raise ValueError("empty embedding payload")

        evidence_count = self._get_track_evidence_count(payload, track_embeddings)
        payload_consistency = self._get_payload_consistency(payload)
        self._validate_track_evidence(
            evidence_count=evidence_count,
            track_embeddings=track_embeddings,
            payload_consistency=payload_consistency,
        )

        track_embedding = self._aggregate_track_embeddings(track_embeddings)
        ranked_candidates = self._rank_candidates(
            users=users,
            track_embedding=track_embedding,
            track_embeddings=track_embeddings,
            evidence_count=evidence_count,
            payload_consistency=payload_consistency,
        )

        if not ranked_candidates:
            return None

        best_candidate = ranked_candidates[0]
        second_best_candidate = ranked_candidates[1] if len(ranked_candidates) > 1 else None
        score_margin = self._get_score_margin(best_candidate, second_best_candidate)
        decision, rejection_reason = self._decide_match(
            best_candidate=best_candidate,
            second_best_candidate=second_best_candidate,
            score_margin=score_margin,
        )

        logger.info(
            "Decision for track_id=%s user_id=%s best_score=%.4f second_best_score=%s margin=%s decision=%s",
            payload.get("track_id"),
            best_candidate.user_id,
            best_candidate.final_score,
            "n/a" if second_best_candidate is None else f"{second_best_candidate.final_score:.4f}",
            "n/a" if score_margin is None else f"{score_margin:.4f}",
            "accepted" if decision else f"rejected ({rejection_reason})",
        )

        if not decision:
            logger.info(
                "Rejected track_id=%s user_id=%s "
                "(distance=%.4f mean=%.4f max=%.4f std=%.4f score=%.4f embeddings_used=%d evidence_count=%d payload_consistency=%s)",
                payload.get("track_id"),
                best_candidate.user_id,
                best_candidate.aggregated_distance,
                best_candidate.mean_distance,
                best_candidate.max_distance,
                best_candidate.distance_std,
                best_candidate.final_score,
                len(track_embeddings),
                evidence_count,
                "n/a" if payload_consistency is None else f"{payload_consistency:.4f}",
            )
            return None

        confidence = max(0.0, min(1.0, 1.0 - best_candidate.aggregated_distance))
        return MatchResult(
            user_id=best_candidate.user_id,
            track_id=str(payload["track_id"]),
            camera_id=payload.get("camera_id"),
            video_id=self._get_video_id(payload),
            source_video_s3=payload.get("source_video_s3"),
            timestamp=self._get_timestamp(payload),
            keyframe=self._get_keyframe(payload),
            keyframe_s3=payload.get("keyframe_s3") or payload.get("keyframe"),
            score=best_candidate.final_score,
            confidence=confidence,
            min_distance=best_candidate.aggregated_distance,
            mean_distance=best_candidate.mean_distance,
            max_distance=best_candidate.max_distance,
            distance_std=best_candidate.distance_std,
            embeddings_used=len(track_embeddings),
            track_evidence_count=evidence_count,
            payload_consistency=payload_consistency,
            second_best_score=None if second_best_candidate is None else second_best_candidate.final_score,
            score_margin=score_margin,
        )

    def _extract_track_embeddings(self, payload: dict[str, Any]) -> np.ndarray:
        raw_embeddings = payload.get("embeddings")
        if raw_embeddings is None:
            raw_embeddings = payload.get("embedding")
        if raw_embeddings is None:
            raw_embeddings = payload.get("face_embedding")

        return normalize_embeddings(raw_embeddings)

    def _get_track_evidence_count(
        self,
        payload: dict[str, Any],
        track_embeddings: np.ndarray,
    ) -> int:
        reported_count = payload.get("num_faces_detected")
        if reported_count is None:
            reported_count = payload.get("num_embeddings")

        if reported_count is None:
            return len(track_embeddings)

        try:
            return max(int(reported_count), len(track_embeddings))
        except (TypeError, ValueError):
            return len(track_embeddings)

    def _get_payload_consistency(self, payload: dict[str, Any]) -> float | None:
        raw_value = payload.get("consistency")
        if raw_value is None:
            return None

        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    def _validate_track_evidence(
        self,
        evidence_count: int,
        track_embeddings: np.ndarray,
        payload_consistency: float | None,
    ) -> None:
        if len(track_embeddings) >= self.config.min_track_embeddings:
            return

        if evidence_count >= self.config.min_track_embeddings:
            if payload_consistency is None:
                raise ValueError("track has aggregated embedding without consistency metadata")
            if payload_consistency < self.config.min_track_consistency:
                raise ValueError("track consistency below minimum threshold")
            return

        if len(track_embeddings) < 2:
            raise ValueError("single-embedding tracks are not eligible for matching")

    def _aggregate_track_embeddings(self, embeddings: np.ndarray) -> np.ndarray:
        aggregated = np.mean(embeddings, axis=0)
        normalized = normalize_embeddings(aggregated)
        if normalized.size == 0:
            raise ValueError("track embedding could not be normalized")
        return normalized

    def _rank_candidates(
        self,
        users: list[dict[str, Any]],
        track_embedding: np.ndarray,
        track_embeddings: np.ndarray,
        evidence_count: int,
        payload_consistency: float | None,
    ) -> list[CandidateScore]:
        ranked_scores: list[CandidateScore] = []

        for user in users:
            score = self._score_candidate(
                user=user,
                track_embedding=track_embedding,
                track_embeddings=track_embeddings,
                evidence_count=evidence_count,
                payload_consistency=payload_consistency,
            )
            ranked_scores.append(score)

        ranked_scores.sort(key=lambda candidate: candidate.final_score, reverse=True)
        return ranked_scores

    def _normalize_score(self, raw_score: float) -> float:
        return float(np.clip(raw_score, 0.0, 1.0))

    def _score_candidate(
        self,
        user: dict[str, Any],
        track_embedding: np.ndarray,
        track_embeddings: np.ndarray,
        evidence_count: int,
        payload_consistency: float | None,
    ) -> CandidateScore:
        aggregated_distance = float(
            cdist(user["embeddings"], track_embedding, metric="euclidean").min()
        )

        per_embedding_distances = cdist(
            track_embeddings,
            user["embeddings"],
            metric="euclidean",
        ).min(axis=1)

        mean_distance = float(np.mean(per_embedding_distances))
        distance_std = float(np.std(per_embedding_distances))
        max_distance = float(np.max(per_embedding_distances))
        raw_score = ((1.0 - aggregated_distance) * 0.6) + ((1.0 - mean_distance) * 0.3) - (distance_std * 0.1)
        final_score = self._normalize_score(raw_score)
        passes_consistency = self._is_consistent_match(
            track_embeddings=track_embeddings,
            candidate_score=CandidateScore(
                user_id=user["user_id"],
                aggregated_distance=aggregated_distance,
                mean_distance=mean_distance,
                max_distance=max_distance,
                distance_std=distance_std,
                per_embedding_distances=per_embedding_distances.astype(float).tolist(),
                final_score=final_score,
                passes_consistency=False,
            ),
            evidence_count=evidence_count,
            payload_consistency=payload_consistency,
        )

        return CandidateScore(
            user_id=user["user_id"],
            aggregated_distance=aggregated_distance,
            mean_distance=mean_distance,
            max_distance=max_distance,
            distance_std=distance_std,
            per_embedding_distances=per_embedding_distances.astype(float).tolist(),
            final_score=final_score,
            passes_consistency=passes_consistency,
        )

    def _is_consistent_match(
        self,
        track_embeddings: np.ndarray,
        candidate_score: CandidateScore,
        evidence_count: int,
        payload_consistency: float | None,
    ) -> bool:
        if candidate_score.aggregated_distance > self.config.match_threshold:
            return False

        if len(track_embeddings) >= 2:
            if candidate_score.mean_distance > self.config.match_threshold:
                return False
            if candidate_score.max_distance > self.config.match_threshold:
                return False
            if candidate_score.distance_std > self.config.max_distance_std:
                return False
            return True

        if evidence_count >= self.config.min_track_embeddings:
            return (
                payload_consistency is not None
                and payload_consistency >= self.config.min_track_consistency
            )

        return False

    def _get_score_margin(
        self,
        best_candidate: CandidateScore,
        second_best_candidate: CandidateScore | None,
    ) -> float | None:
        if second_best_candidate is None:
            return None
        return best_candidate.final_score - second_best_candidate.final_score

    def _decide_match(
        self,
        best_candidate: CandidateScore,
        second_best_candidate: CandidateScore | None,
        score_margin: float | None,
    ) -> tuple[bool, str | None]:
        if not best_candidate.passes_consistency:
            return False, "consistency"
        if best_candidate.final_score < self.config.min_score:
            return False, "min_score"
        if second_best_candidate is not None and score_margin is not None and score_margin < self.config.margin:
            return False, "margin"
        return True, None

    def _get_video_id(self, payload: dict[str, Any]) -> str | None:
        return payload.get("video_id") or payload.get("source_video_id")

    def _get_timestamp(self, payload: dict[str, Any]) -> str | None:
        return payload.get("timestamp") or payload.get("start_time")

    def _get_keyframe(self, payload: dict[str, Any]) -> str | None:
        return payload.get("keyframe") or payload.get("keyframe_s3")
