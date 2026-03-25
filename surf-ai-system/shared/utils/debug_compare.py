from __future__ import annotations

from typing import Any

from shared.utils.embeddings import pairwise_cosine_similarity, pairwise_euclidean_distances
from shared.utils.match_decision import (
    build_candidate_users_from_reference_images,
    CandidateScore,
    evaluate_track_match,
)


def _serialize_top_candidates(
    ranked_candidates: list[CandidateScore],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not ranked_candidates:
        return []

    best_similarity = float(ranked_candidates[0].best_similarity)
    return [
        {
            "rank": index,
            "user_id": candidate.user_id,
            "user_embedding_id": candidate.best_user_embedding_id,
            "score": float(candidate.best_similarity),
            "similarity": float(candidate.best_similarity),
            "distance": float(candidate.aggregated_distance),
            "final_score": float(candidate.final_score),
            "margin": float(best_similarity - candidate.best_similarity),
            "embeddings_used": int(candidate.embeddings_compared),
        }
        for index, candidate in enumerate(ranked_candidates[: max(int(limit), 1)], start=1)
    ]


def build_debug_compare_response(
    *,
    video_id: str,
    video: dict[str, Any],
    pool: dict[str, Any] | None,
    pool_users: list[dict[str, Any]],
    pool_reference_images: list[dict[str, Any]],
    video_embeddings: list[dict[str, Any]],
    frame_embeddings: list[dict[str, Any]],
    debug_frames: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    similarity_threshold: float,
    margin_threshold: float,
    min_track_embeddings: int = 3,
    min_track_consistency: float = 0.75,
    matching_attempts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frame_embeddings_lookup = {
        (item["track_id"], int(item["frame_index"])): item
        for item in frame_embeddings
    }
    reference_lookup = {
        item["user_embedding_id"]: item
        for item in pool_reference_images
    }
    user_lookup = {user["user_id"]: user for user in pool_users}
    candidate_users = build_candidate_users_from_reference_images(pool_reference_images)
    matching_attempts = matching_attempts or {}
    debug_frames_by_track: dict[str, list[dict[str, Any]]] = {}
    for frame in debug_frames:
        debug_frames_by_track.setdefault(frame["track_id"], []).append(frame)

    comparisons: list[dict[str, Any]] = []
    best_reference_by_track: dict[str, dict[str, Any]] = {}
    if pool_reference_images and video_embeddings:
        user_vectors = [item["embedding"] for item in pool_reference_images]
        video_vectors = [item["embedding"] for item in video_embeddings]
        distances = pairwise_euclidean_distances(video_vectors, user_vectors)
        similarities = pairwise_cosine_similarity(video_vectors, user_vectors)

        for video_index, video_embedding in enumerate(video_embeddings):
            ranked_reference_indexes = sorted(
                range(len(pool_reference_images)),
                key=lambda index: (
                    float(similarities[video_index, index]),
                    -float(distances[video_index, index]),
                ),
                reverse=True,
            )
            for reference_index in ranked_reference_indexes:
                reference_image = pool_reference_images[reference_index]
                distance = float(distances[video_index, reference_index])
                similarity = float(similarities[video_index, reference_index])
                comparisons.append(
                    {
                        "video_embedding_id": video_embedding["video_embedding_id"],
                        "track_id": video_embedding["track_id"],
                        "user_embedding_id": reference_image["user_embedding_id"],
                        "user_id": reference_image["user_id"],
                        "user_email": reference_image["email"],
                        "distance": distance,
                        "similarity": similarity,
                        "is_match_under_threshold": similarity >= similarity_threshold,
                    }
                )

            best_index = ranked_reference_indexes[0]
            second_index = ranked_reference_indexes[1] if len(ranked_reference_indexes) > 1 else None
            best_reference = pool_reference_images[best_index]
            best_distance = float(distances[video_index, best_index])
            best_similarity = float(similarities[video_index, best_index])
            second_best_similarity = (
                None if second_index is None else float(similarities[video_index, second_index])
            )
            evaluation = evaluate_track_match(
                track_embedding=video_embedding["embedding"],
                users=candidate_users,
                similarity_threshold=similarity_threshold,
                margin_threshold=margin_threshold,
                min_track_embeddings=min_track_embeddings,
                min_track_consistency=min_track_consistency,
                evidence_count=int(
                    video_embedding.get("frames_count")
                    or video_embedding.get("embeddings_created")
                    or 0
                ),
                payload_consistency=video_embedding.get("consistency"),
                quality_avg=video_embedding.get("quality_avg"),
            )
            decision = evaluation.decision
            best_candidate = evaluation.best_candidate
            second_best_candidate = evaluation.second_best_candidate
            track_debug_frames = debug_frames_by_track.get(video_embedding["track_id"], [])
            used_frame_indexes = sorted(
                int(frame["frame_index"])
                for frame in track_debug_frames
                if frame.get("used_for_embedding")
            )
            current_attempt = matching_attempts.get(video_embedding["track_id"]) or {}
            current_top_candidates = _serialize_top_candidates(evaluation.ranked_candidates)
            best_reference_by_track[video_embedding["track_id"]] = {
                "video_embedding_id": video_embedding["video_embedding_id"],
                "track_id": video_embedding["track_id"],
                "keyframe_s3": video_embedding.get("keyframe_s3"),
                "keyframe_url": video_embedding.get("keyframe_url"),
                "start_time": video_embedding.get("start_time"),
                "end_time": video_embedding.get("end_time"),
                "frames_count": video_embedding.get("frames_count"),
                "frames_received": video_embedding.get("frames_received"),
                "embeddings_created": video_embedding.get("embeddings_created"),
                "quality_avg": video_embedding.get("quality_avg"),
                "consistency": video_embedding.get("consistency"),
                "aggregation_method": video_embedding.get("aggregation_method"),
                "used_frame_indexes": used_frame_indexes,
                "best_user_id": None if best_candidate is None else best_candidate.user_id,
                "best_user_email": None
                if best_candidate is None
                else user_lookup.get(best_candidate.user_id, {}).get("email"),
                "best_user_embedding_id": None if best_candidate is None else best_candidate.best_user_embedding_id,
                "best_reference_image_url": None
                if best_candidate is None
                else reference_lookup.get(best_candidate.best_user_embedding_id, {}).get("source_image_url"),
                "distance": None if best_candidate is None else float(best_candidate.aggregated_distance),
                "similarity": None if decision.best_similarity is None else float(decision.best_similarity),
                "second_best_similarity": decision.second_best_similarity,
                "similarity_margin": decision.margin,
                "margin": decision.margin,
                "passes_similarity": decision.passes_similarity,
                "passes_margin": decision.passes_margin,
                "final_verdict": decision.final_verdict,
                "decision": decision.final_verdict,
                "decision_reason": decision.decision_reason,
                "decision_explanation": decision.explanation,
                "threshold_used": decision.threshold_used,
                "margin_threshold_used": decision.margin_threshold_used,
                "is_match_under_threshold": decision.final_verdict == "match",
                "match_rejection_reason": decision.decision_reason,
                "last_attempt": current_attempt or None,
                "last_attempt_outcome": current_attempt.get("persist_status"),
                "last_attempt_at": current_attempt.get("processed_at"),
                "current_best_pairwise_similarity": best_similarity,
                "current_best_pairwise_distance": best_distance,
                "current_pairwise_second_best_similarity": second_best_similarity,
                "current_evaluation_candidates": len(evaluation.ranked_candidates),
                "current_second_best_user_id": None
                if second_best_candidate is None
                else second_best_candidate.user_id,
                "current_top_candidates": current_top_candidates,
                "last_attempt_top_candidates": current_attempt.get("top_candidates") or [],
            }
        comparisons.sort(key=lambda item: (item["similarity"], -item["distance"]), reverse=True)

    video_frames = [
        {
            "video_embedding_id": item["video_embedding_id"],
            "track_id": item["track_id"],
            "keyframe_s3": item.get("keyframe_s3"),
            "keyframe_url": item.get("keyframe_url"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "frames_count": item.get("frames_count"),
            "frames_received": item.get("frames_received"),
            "embeddings_created": item.get("embeddings_created"),
            "quality_avg": item.get("quality_avg"),
            "consistency": item.get("consistency"),
            "aggregation_method": item.get("aggregation_method"),
            "used_frame_indexes": best_reference_by_track.get(item["track_id"], {}).get("used_frame_indexes", []),
            "best_user_id": best_reference_by_track.get(item["track_id"], {}).get("best_user_id"),
            "best_user_email": best_reference_by_track.get(item["track_id"], {}).get("best_user_email"),
            "best_user_embedding_id": best_reference_by_track.get(item["track_id"], {}).get("best_user_embedding_id"),
            "best_reference_image_url": best_reference_by_track.get(item["track_id"], {}).get("best_reference_image_url"),
            "distance": best_reference_by_track.get(item["track_id"], {}).get("distance"),
            "similarity": best_reference_by_track.get(item["track_id"], {}).get("similarity"),
            "second_best_similarity": best_reference_by_track.get(item["track_id"], {}).get("second_best_similarity"),
            "similarity_margin": best_reference_by_track.get(item["track_id"], {}).get("similarity_margin"),
            "margin": best_reference_by_track.get(item["track_id"], {}).get("margin"),
            "passes_similarity": best_reference_by_track.get(item["track_id"], {}).get("passes_similarity"),
            "passes_margin": best_reference_by_track.get(item["track_id"], {}).get("passes_margin"),
            "final_verdict": best_reference_by_track.get(item["track_id"], {}).get("final_verdict"),
            "decision_reason": best_reference_by_track.get(item["track_id"], {}).get("decision_reason"),
            "decision_explanation": best_reference_by_track.get(item["track_id"], {}).get("decision_explanation"),
            "threshold_used": best_reference_by_track.get(item["track_id"], {}).get("threshold_used"),
            "margin_threshold_used": best_reference_by_track.get(item["track_id"], {}).get("margin_threshold_used"),
            "decision": best_reference_by_track.get(item["track_id"], {}).get("decision"),
            "is_match_under_threshold": best_reference_by_track.get(item["track_id"], {}).get("is_match_under_threshold", False),
            "match_rejection_reason": best_reference_by_track.get(item["track_id"], {}).get("match_rejection_reason"),
            "last_attempt": best_reference_by_track.get(item["track_id"], {}).get("last_attempt"),
            "last_attempt_outcome": best_reference_by_track.get(item["track_id"], {}).get("last_attempt_outcome"),
            "last_attempt_at": best_reference_by_track.get(item["track_id"], {}).get("last_attempt_at"),
            "current_best_pairwise_similarity": best_reference_by_track.get(item["track_id"], {}).get("current_best_pairwise_similarity"),
            "current_best_pairwise_distance": best_reference_by_track.get(item["track_id"], {}).get("current_best_pairwise_distance"),
            "current_pairwise_second_best_similarity": best_reference_by_track.get(item["track_id"], {}).get("current_pairwise_second_best_similarity"),
            "current_evaluation_candidates": best_reference_by_track.get(item["track_id"], {}).get("current_evaluation_candidates"),
            "current_second_best_user_id": best_reference_by_track.get(item["track_id"], {}).get("current_second_best_user_id"),
            "current_top_candidates": best_reference_by_track.get(item["track_id"], {}).get("current_top_candidates", []),
            "last_attempt_top_candidates": best_reference_by_track.get(item["track_id"], {}).get("last_attempt_top_candidates", []),
        }
        for item in video_embeddings
    ]
    video_frames.sort(
        key=lambda item: (
            0 if item.get("is_match_under_threshold") else 1,
            1.0 if item.get("similarity") is None else -float(item["similarity"]),
            float("inf") if item.get("distance") is None else float(item["distance"]),
        )
    )

    debug_frame_results: list[dict[str, Any]] = []
    for frame in debug_frames:
        best_track_reference = best_reference_by_track.get(frame["track_id"])
        reference_image = None
        distance = None
        similarity = None
        if best_track_reference is not None:
            reference_image = reference_lookup.get(best_track_reference["best_user_embedding_id"])
        if reference_image is None and frame.get("embedding") is not None and pool_reference_images:
            user_vectors = [item["embedding"] for item in pool_reference_images]
            frame_distances = pairwise_euclidean_distances([frame["embedding"]], user_vectors)
            frame_similarities = pairwise_cosine_similarity([frame["embedding"]], user_vectors)
            best_match_index = int(frame_similarities[0].argmax())
            reference_image = pool_reference_images[best_match_index]
            distance = float(frame_distances[0, best_match_index])
            similarity = float(frame_similarities[0, best_match_index])
        elif reference_image is not None and frame.get("embedding") is not None:
            distance = float(
                pairwise_euclidean_distances([frame["embedding"]], [reference_image["embedding"]])[0, 0]
            )
            similarity = float(
                pairwise_cosine_similarity([frame["embedding"]], [reference_image["embedding"]])[0, 0]
            )

        frame_embedding = frame_embeddings_lookup.get((frame["track_id"], int(frame["frame_index"])))
        debug_frame_results.append(
            {
                "debug_frame_id": frame["debug_frame_id"],
                "video_embedding_id": frame.get("video_embedding_id")
                or (None if frame_embedding is None else frame_embedding.get("video_embedding_id")),
                "track_id": frame["track_id"],
                "frame_index": frame["frame_index"],
                "frame_timestamp": frame.get("frame_timestamp"),
                "image_url": frame.get("image_url"),
                "bbox": frame.get("bbox"),
                "face_bbox": frame.get("face_bbox"),
                "quality_score": frame.get("quality_score")
                if frame.get("quality_score") is not None
                else None if frame_embedding is None else frame_embedding.get("quality_score"),
                "det_score": frame.get("det_score"),
                "face_size": frame.get("face_size"),
                "blur_score": frame.get("blur_score"),
                "rejection_reason": frame.get("rejection_reason"),
                "has_face": frame.get("has_face", False),
                "is_valid": frame.get("is_valid", False),
                "used_for_embedding": frame.get("used_for_embedding", False),
                "user_id": None if reference_image is None else reference_image["user_id"],
                "user_email": None if reference_image is None else reference_image["email"],
                "user_embedding_id": None if reference_image is None else reference_image["user_embedding_id"],
                "distance": distance,
                "similarity": similarity,
                "is_match_under_threshold": False if similarity is None else similarity >= similarity_threshold,
            }
        )

    debug_frame_results.sort(key=lambda item: (item["track_id"], item["frame_index"]))

    best_reference_image_url = None
    best_reference_user_embedding_id = None
    best_match_user_id = None
    best_match_user_email = None
    if video_frames:
        best_track = video_frames[0]
        best_reference_image_url = best_track.get("best_reference_image_url")
        best_reference_user_embedding_id = best_track.get("best_user_embedding_id")
        best_match_user_id = best_track.get("best_user_id")
        best_match_user_email = best_track.get("best_user_email")

    assigned_user = user_lookup.get(video.get("assigned_user_id")) if video.get("assigned_user_id") else None

    return {
        "video_id": video_id,
        "pool_id": video.get("pool_id"),
        "pool": pool,
        "pool_users": len(pool_users),
        "user_embeddings": len(pool_reference_images),
        "video_embeddings": len(video_embeddings),
        "comparisons": comparisons,
        "best_match_user_id": best_match_user_id,
        "best_match_user_email": best_match_user_email,
        "best_reference_user_embedding_id": best_reference_user_embedding_id,
        "best_reference_image_url": best_reference_image_url,
        "reference_images": [
            {
                "user_embedding_id": item["user_embedding_id"],
                "user_id": item["user_id"],
                "user_email": item["email"],
                "image_url": item.get("source_image_url"),
                "created_at": item.get("created_at"),
            }
            for item in pool_reference_images
            if item.get("source_image_url")
        ],
        "track_summaries": video_frames,
        "video_frames": video_frames,
        "debug_frames": debug_frame_results,
        "matches": matches,
        "matching_attempts": matching_attempts,
        "assigned_user_id": video.get("assigned_user_id"),
        "assigned_user_email": None if assigned_user is None else assigned_user["email"],
        "summary": {
            "total_frames": len(debug_frame_results),
            "valid_frames": sum(1 for item in debug_frame_results if item.get("is_valid")),
            "used_frames": sum(1 for item in debug_frame_results if item.get("used_for_embedding")),
            "tracks": len(video_frames),
            "matched_tracks": sum(1 for item in video_frames if item.get("is_match_under_threshold")),
            "rejected_tracks": sum(1 for item in video_frames if not item.get("is_match_under_threshold")),
            "rejected_low_similarity": sum(1 for item in video_frames if item.get("decision_reason") == "min_similarity"),
            "rejected_low_margin": sum(1 for item in video_frames if item.get("decision_reason") == "min_margin"),
            "rejected_min_frames": sum(1 for item in video_frames if item.get("decision_reason") == "min_frames_per_track"),
            "rejected_track_consistency": sum(1 for item in video_frames if item.get("decision_reason") == "track_consistency"),
            "rejected_candidate_consistency": sum(1 for item in video_frames if item.get("decision_reason") == "consistency"),
            "best_similarity": None if not video_frames else video_frames[0].get("similarity"),
            "best_distance": None if not video_frames else video_frames[0].get("distance"),
            "force_match": False,
        },
        "threshold": similarity_threshold,
        "similarity_threshold": similarity_threshold,
        "margin_threshold": margin_threshold,
    }
