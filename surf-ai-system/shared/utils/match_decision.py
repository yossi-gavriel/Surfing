from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from shared.utils.embeddings import (
    normalize_embeddings,
    pairwise_cosine_similarity,
    pairwise_euclidean_distances,
)


@dataclass(frozen=True)
class MatchDecision:
    best_similarity: float | None
    second_best_similarity: float | None
    margin: float | None
    threshold_used: float
    margin_threshold_used: float
    passes_similarity: bool
    passes_margin: bool
    final_verdict: str
    decision_reason: str | None
    explanation: str


@dataclass(frozen=True)
class CandidateScore:
    user_id: str
    best_user_embedding_id: str | None
    embeddings_compared: int
    aggregated_distance: float
    best_similarity: float
    mean_distance: float
    max_distance: float
    distance_std: float
    final_score: float
    passes_consistency: bool


@dataclass(frozen=True)
class TrackMatchEvaluation:
    decision: MatchDecision
    best_candidate: CandidateScore | None
    second_best_candidate: CandidateScore | None
    ranked_candidates: list[CandidateScore]
    validation_error: str | None
    validation_reason: str | None


def evaluate_match_decision(
    *,
    best_similarity: float | None,
    second_best_similarity: float | None,
    similarity_threshold: float,
    margin_threshold: float,
    estimate_margin_when_missing: bool = False,
) -> MatchDecision:
    margin = None
    if best_similarity is not None and second_best_similarity is not None:
        margin = float(best_similarity - second_best_similarity)
    elif best_similarity is not None and estimate_margin_when_missing:
        margin = float(best_similarity - similarity_threshold)

    if best_similarity is None:
        return MatchDecision(
            best_similarity=None,
            second_best_similarity=second_best_similarity,
            margin=margin,
            threshold_used=float(similarity_threshold),
            margin_threshold_used=float(margin_threshold),
            passes_similarity=False,
            passes_margin=False,
            final_verdict="no_match",
            decision_reason="no_reference_images",
            explanation="Match rejected because no candidate reference images were available",
        )

    passes_similarity = float(best_similarity) >= float(similarity_threshold)
    if second_best_similarity is None and not estimate_margin_when_missing:
        passes_margin = True
    else:
        passes_margin = margin is not None and float(margin) >= float(margin_threshold)

    if not passes_similarity:
        return MatchDecision(
            best_similarity=float(best_similarity),
            second_best_similarity=second_best_similarity,
            margin=margin,
            threshold_used=float(similarity_threshold),
            margin_threshold_used=float(margin_threshold),
            passes_similarity=False,
            passes_margin=passes_margin,
            final_verdict="no_match",
            decision_reason="min_similarity",
            explanation="Match rejected because similarity < threshold",
        )

    if not passes_margin:
        return MatchDecision(
            best_similarity=float(best_similarity),
            second_best_similarity=second_best_similarity,
            margin=margin,
            threshold_used=float(similarity_threshold),
            margin_threshold_used=float(margin_threshold),
            passes_similarity=True,
            passes_margin=False,
            final_verdict="no_match",
            decision_reason="min_margin",
            explanation="Match rejected because margin too small",
        )

    return MatchDecision(
        best_similarity=float(best_similarity),
        second_best_similarity=second_best_similarity,
        margin=margin,
        threshold_used=float(similarity_threshold),
        margin_threshold_used=float(margin_threshold),
        passes_similarity=True,
        passes_margin=True,
        final_verdict="match",
        decision_reason=None,
        explanation="Match accepted because similarity and margin both passed",
    )


def build_candidate_users_from_reference_images(
    reference_images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in reference_images:
        user_id = str(item.get("user_id") or "")
        if not user_id:
            continue
        grouped.setdefault(
            user_id,
            {
                "user_id": user_id,
                "email": item.get("email"),
                "embedding_ids": [],
                "raw_embeddings": [],
            },
        )
        grouped[user_id]["embedding_ids"].append(
            str(item.get("user_embedding_id") or item.get("reference_image_id") or "")
        )
        grouped[user_id]["raw_embeddings"].append(item.get("embedding"))

    users: list[dict[str, Any]] = []
    for group in grouped.values():
        embeddings = normalize_embeddings(group["raw_embeddings"])
        if embeddings.size == 0:
            continue
        users.append(
            {
                "user_id": group["user_id"],
                "email": group.get("email"),
                "embedding_ids": group["embedding_ids"][: embeddings.shape[0]],
                "embeddings": embeddings,
                "avg_embedding": normalize_embeddings(np.mean(embeddings, axis=0))[0],
            }
        )
    return users


def evaluate_track_match(
    *,
    track_embedding: Any,
    users: list[dict[str, Any]],
    similarity_threshold: float,
    margin_threshold: float,
    min_track_embeddings: int,
    min_track_consistency: float,
    evidence_count: int,
    payload_consistency: float | None,
    quality_avg: float | None,
) -> TrackMatchEvaluation:
    normalized_track_embedding = normalize_embeddings(track_embedding)
    if normalized_track_embedding.size == 0:
        raise ValueError("track embedding could not be normalized")
    track_vector = normalized_track_embedding[0]

    if not users:
        return TrackMatchEvaluation(
            decision=evaluate_match_decision(
                best_similarity=None,
                second_best_similarity=None,
                similarity_threshold=similarity_threshold,
                margin_threshold=margin_threshold,
            ),
            best_candidate=None,
            second_best_candidate=None,
            ranked_candidates=[],
            validation_error=None,
            validation_reason=None,
        )

    if evidence_count < int(min_track_embeddings):
        decision = MatchDecision(
            best_similarity=None,
            second_best_similarity=None,
            margin=None,
            threshold_used=float(similarity_threshold),
            margin_threshold_used=float(margin_threshold),
            passes_similarity=False,
            passes_margin=False,
            final_verdict="no_match",
            decision_reason="min_frames_per_track",
            explanation="Match rejected because track has fewer than the minimum frames required",
        )
        return TrackMatchEvaluation(
            decision=decision,
            best_candidate=None,
            second_best_candidate=None,
            ranked_candidates=[],
            validation_error="track has fewer than minimum supported frames",
            validation_reason=decision.decision_reason,
        )

    if payload_consistency is not None and float(payload_consistency) < float(min_track_consistency):
        decision = MatchDecision(
            best_similarity=None,
            second_best_similarity=None,
            margin=None,
            threshold_used=float(similarity_threshold),
            margin_threshold_used=float(margin_threshold),
            passes_similarity=False,
            passes_margin=False,
            final_verdict="no_match",
            decision_reason="track_consistency",
            explanation="Match rejected because track consistency is below the minimum threshold",
        )
        return TrackMatchEvaluation(
            decision=decision,
            best_candidate=None,
            second_best_candidate=None,
            ranked_candidates=[],
            validation_error="track consistency below minimum threshold",
            validation_reason=decision.decision_reason,
        )

    ranked_candidates: list[CandidateScore] = []
    for user in users:
        user_embeddings = normalize_embeddings(user.get("embeddings"))
        if user_embeddings.size == 0:
            continue

        distances = pairwise_euclidean_distances(user_embeddings, track_vector).reshape(-1)
        similarities = pairwise_cosine_similarity(user_embeddings, track_vector).reshape(-1)
        if distances.size == 0 or similarities.size == 0:
            continue

        best_index = int(np.argmin(distances))
        best_distance = float(distances[best_index])
        best_similarity = float(similarities[best_index])
        mean_distance = float(np.mean(distances))
        max_distance = float(np.max(distances))
        distance_std = float(np.std(distances))
        quality_multiplier = 1.0 if quality_avg is None else 0.95 + (min(max(float(quality_avg), 0.0), 1.0) * 0.05)
        final_score = float(np.clip(best_similarity * quality_multiplier, 0.0, 1.0))
        embedding_ids = list(user.get("embedding_ids") or [])
        best_user_embedding_id = embedding_ids[best_index] if best_index < len(embedding_ids) else None
        ranked_candidates.append(
            CandidateScore(
                user_id=str(user["user_id"]),
                best_user_embedding_id=None if best_user_embedding_id is None else str(best_user_embedding_id),
                embeddings_compared=int(user_embeddings.shape[0]),
                aggregated_distance=best_distance,
                best_similarity=best_similarity,
                mean_distance=mean_distance,
                max_distance=max_distance,
                distance_std=distance_std,
                final_score=final_score,
                passes_consistency=True,
            )
        )

    ranked_candidates.sort(
        key=lambda candidate: (
            candidate.best_similarity,
            candidate.final_score,
            -candidate.aggregated_distance,
        ),
        reverse=True,
    )

    if not ranked_candidates:
        return TrackMatchEvaluation(
            decision=evaluate_match_decision(
                best_similarity=None,
                second_best_similarity=None,
                similarity_threshold=similarity_threshold,
                margin_threshold=margin_threshold,
            ),
            best_candidate=None,
            second_best_candidate=None,
            ranked_candidates=[],
            validation_error=None,
            validation_reason=None,
        )

    best_candidate = ranked_candidates[0]
    second_best_candidate = ranked_candidates[1] if len(ranked_candidates) > 1 else None
    if not best_candidate.passes_consistency:
        margin = None
        second_best_similarity = None
        if second_best_candidate is not None:
            second_best_similarity = float(second_best_candidate.best_similarity)
            margin = float(best_candidate.best_similarity - second_best_candidate.best_similarity)
        decision = MatchDecision(
            best_similarity=float(best_candidate.best_similarity),
            second_best_similarity=second_best_similarity,
            margin=margin,
            threshold_used=float(similarity_threshold),
            margin_threshold_used=float(margin_threshold),
            passes_similarity=False,
            passes_margin=False,
            final_verdict="no_match",
            decision_reason="consistency",
            explanation="Match rejected because track consistency checks failed",
        )
    else:
        decision = evaluate_match_decision(
            best_similarity=best_candidate.best_similarity,
            second_best_similarity=None if second_best_candidate is None else second_best_candidate.best_similarity,
            similarity_threshold=similarity_threshold,
            margin_threshold=margin_threshold,
        )

    return TrackMatchEvaluation(
        decision=decision,
        best_candidate=best_candidate,
        second_best_candidate=second_best_candidate,
        ranked_candidates=ranked_candidates,
        validation_error=None,
        validation_reason=None,
    )
