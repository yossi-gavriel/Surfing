from __future__ import annotations

from typing import Any

import numpy as np


def normalize_embedding_vector(value: Any) -> np.ndarray | None:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    if vector.size == 0:
        return None

    norm = float(np.linalg.norm(vector))
    if norm <= 0:
        return None

    return vector / norm


def normalize_embeddings(value: Any) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.size == 0:
        return np.empty((0, 0), dtype=np.float32)

    if array.ndim == 1:
        array = array.reshape(1, -1)

    normalized_rows: list[np.ndarray] = []
    for row in array:
        normalized = normalize_embedding_vector(row)
        if normalized is not None:
            normalized_rows.append(normalized)

    if not normalized_rows:
        return np.empty((0, 0), dtype=np.float32)

    return np.vstack(normalized_rows).astype(np.float32)


def pairwise_euclidean_distances(left: Any, right: Any) -> np.ndarray:
    left_array = normalize_embeddings(left)
    right_array = normalize_embeddings(right)

    left_rows = left_array.shape[0] if left_array.ndim == 2 else 0
    right_rows = right_array.shape[0] if right_array.ndim == 2 else 0
    if left_rows == 0 or right_rows == 0:
        return np.empty((left_rows, right_rows), dtype=np.float32)

    differences = left_array[:, None, :] - right_array[None, :, :]
    return np.linalg.norm(differences, axis=2).astype(np.float32)


def pairwise_cosine_similarity(left: Any, right: Any) -> np.ndarray:
    left_array = normalize_embeddings(left)
    right_array = normalize_embeddings(right)

    left_rows = left_array.shape[0] if left_array.ndim == 2 else 0
    right_rows = right_array.shape[0] if right_array.ndim == 2 else 0
    if left_rows == 0 or right_rows == 0:
        return np.empty((left_rows, right_rows), dtype=np.float32)

    similarity = left_array @ right_array.T
    return np.clip(similarity, -1.0, 1.0).astype(np.float32)
