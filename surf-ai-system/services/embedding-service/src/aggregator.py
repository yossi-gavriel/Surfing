import numpy as np


def cosine_similarity(e1, e2):
    n1 = np.linalg.norm(e1)
    n2 = np.linalg.norm(e2)
    if n1 == 0 or n2 == 0:
        return 0.0
    return float(np.dot(e1, e2) / (n1 * n2))


class EmbeddingAggregator:
    def __init__(
        self,
        max_similarity=0.95,
        min_samples=3,
        min_quality_score=0.1,
        top_k=5,
    ):
        self.max_similarity = max_similarity
        self.min_samples = min_samples
        self.min_quality_score = min_quality_score
        self.top_k = max(1, top_k)

    def aggregate(self, faces_data):
        evaluation = self.evaluate(faces_data)
        return evaluation["result"]

    def evaluate(self, faces_data, *, min_consistency=None):
        if not faces_data:
            return self._rejected(
                rejection_reason="too_few_frames",
                details={
                    "eligible_frames": 0,
                    "deduplicated_frames": 0,
                    "used_frames_count": 0,
                    "quality_avg": 0.0,
                    "consistency": None,
                },
            )

        eligible = [
            face_data
            for face_data in faces_data
            if face_data.get("eligible_for_aggregation", True)
            and float(face_data.get("quality_score") or 0.0) >= self.min_quality_score
        ]
        if len(eligible) < self.min_samples:
            return self._rejected(
                rejection_reason="too_few_frames",
                details={
                    "eligible_frames": len(eligible),
                    "deduplicated_frames": len(eligible),
                    "used_frames_count": len(eligible),
                    "quality_avg": self._average_quality(eligible),
                    "consistency": None,
                },
            )

        deduplicated = self._deduplicate_by_similarity(eligible)
        if len(deduplicated) < self.min_samples:
            return self._rejected(
                rejection_reason="too_few_frames",
                details={
                    "eligible_frames": len(eligible),
                    "deduplicated_frames": len(deduplicated),
                    "used_frames_count": len(deduplicated),
                    "quality_avg": self._average_quality(deduplicated),
                    "consistency": None,
                },
            )

        top_k = min(5, self.top_k, len(deduplicated))
        selected = sorted(
            deduplicated,
            key=lambda item: float(item.get("quality_score") or 0.0),
            reverse=True,
        )[:top_k]
        if len(selected) < self.min_samples:
            return self._rejected(
                rejection_reason="too_few_frames",
                details={
                    "eligible_frames": len(eligible),
                    "deduplicated_frames": len(deduplicated),
                    "used_frames_count": len(selected),
                    "quality_avg": self._average_quality(selected),
                    "consistency": None,
                },
            )

        embeddings = np.array([item["embedding"] for item in selected], dtype=np.float32)
        aggregated = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(aggregated)
        if norm <= 0:
            return self._rejected(
                rejection_reason="low_quality_score",
                details={
                    "eligible_frames": len(eligible),
                    "deduplicated_frames": len(deduplicated),
                    "used_frames_count": len(selected),
                    "quality_avg": self._average_quality(selected),
                    "consistency": None,
                },
            )
        aggregated = aggregated / norm

        used_frame_indexes = [
            int(item["source_frame_index"])
            for item in selected
            if item.get("source_frame_index") is not None
        ]
        quality_scores = [float(item.get("quality_score") or 0.0) for item in selected]
        det_scores = [float(item.get("det_score") or 0.0) for item in selected]
        consistency = self._consistency(embeddings)
        avg_quality = float(np.mean(quality_scores)) if quality_scores else 0.0
        avg_det = float(np.mean(det_scores)) if det_scores else 0.0
        if avg_quality < self.min_quality_score:
            return self._rejected(
                rejection_reason="low_quality_score",
                details={
                    "eligible_frames": len(eligible),
                    "deduplicated_frames": len(deduplicated),
                    "used_frames_count": len(selected),
                    "quality_avg": avg_quality,
                    "consistency": float(consistency),
                },
            )
        if min_consistency is not None and float(consistency) < float(min_consistency):
            return self._rejected(
                rejection_reason="low_consistency",
                details={
                    "eligible_frames": len(eligible),
                    "deduplicated_frames": len(deduplicated),
                    "used_frames_count": len(selected),
                    "quality_avg": avg_quality,
                    "consistency": float(consistency),
                },
            )
        sample_penalty = min(len(selected) / float(self.min_samples), 1.0)
        final_confidence = avg_det * consistency * sample_penalty

        return {
            "accepted": True,
            "rejection_reason": None,
            "details": {
                "eligible_frames": len(eligible),
                "deduplicated_frames": len(deduplicated),
                "used_frames_count": len(selected),
                "quality_avg": avg_quality,
                "consistency": float(consistency),
            },
            "result": {
            "embedding": aggregated.astype(float).tolist(),
            "confidence": float(final_confidence),
            "frames_count": len(deduplicated),
            "used_frames_count": len(selected),
            "quality_avg": avg_quality,
            "consistency": float(consistency),
            "used_frame_indexes": used_frame_indexes,
            "aggregation_method": "mean_top_k_quality",
            },
        }

    def _deduplicate_by_similarity(self, faces_data):
        deduplicated = []
        for face_data in faces_data:
            embedding = np.asarray(face_data["embedding"], dtype=np.float32)
            duplicate_index = None
            for index, existing in enumerate(deduplicated):
                similarity = cosine_similarity(embedding, np.asarray(existing["embedding"], dtype=np.float32))
                if similarity >= self.max_similarity:
                    duplicate_index = index
                    break
            if duplicate_index is None:
                deduplicated.append(face_data)
                continue

            if float(face_data.get("quality_score") or 0.0) > float(
                deduplicated[duplicate_index].get("quality_score") or 0.0
            ):
                deduplicated[duplicate_index] = face_data
        return deduplicated

    def _consistency(self, embeddings):
        if len(embeddings) < 2:
            return 0.0

        similarities = []
        for index, left in enumerate(embeddings):
            for right in embeddings[index + 1 :]:
                similarities.append(cosine_similarity(left, right))
        return float(np.mean(similarities)) if similarities else 0.0

    def _average_quality(self, faces_data):
        if not faces_data:
            return 0.0
        return float(
            np.mean([float(item.get("quality_score") or 0.0) for item in faces_data])
        )

    def _rejected(self, *, rejection_reason, details):
        return {
            "accepted": False,
            "rejection_reason": rejection_reason,
            "details": details,
            "result": None,
        }
