from __future__ import annotations

from typing import Any

import cv2
import numpy as np


_ARC_FACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def preprocess_face(
    image: np.ndarray,
    *,
    bbox: Any | None = None,
    kps: Any | None = None,
    target_size: tuple[int, int] = (112, 112),
) -> np.ndarray:
    face = _align_face(image=image, kps=kps, target_size=target_size)
    if face is None:
        face = _crop_face(image=image, bbox=bbox)

    if face is None or face.size == 0:
        return np.empty((0, 0, 3), dtype=np.float32)

    resized = cv2.resize(face, target_size, interpolation=cv2.INTER_AREA)
    rgb_face = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return (rgb_face.astype(np.float32) / 255.0).astype(np.float32)


def summarize_face_tensor(face: np.ndarray) -> dict[str, Any]:
    if face.size == 0:
        return {
            "shape": [int(dim) for dim in face.shape],
            "min": None,
            "max": None,
            "mean": None,
        }

    return {
        "shape": [int(dim) for dim in face.shape],
        "min": float(face.min()),
        "max": float(face.max()),
        "mean": float(face.mean()),
    }


def _align_face(
    image: np.ndarray,
    *,
    kps: Any | None,
    target_size: tuple[int, int],
) -> np.ndarray | None:
    if kps is None:
        return None

    points = np.asarray(kps, dtype=np.float32)
    if points.shape != (5, 2):
        return None

    transform, _ = cv2.estimateAffinePartial2D(points, _ARC_FACE_TEMPLATE, method=cv2.LMEDS)
    if transform is None:
        return None

    return cv2.warpAffine(image, transform, target_size, borderValue=0.0)


def _crop_face(
    image: np.ndarray,
    *,
    bbox: Any | None,
) -> np.ndarray | None:
    if bbox is None:
        return image.copy()

    x1, y1, x2, y2 = [int(value) for value in bbox]
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(image.shape[1], x2)
    y2 = min(image.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return None

    return image[y1:y2, x1:x2].copy()
