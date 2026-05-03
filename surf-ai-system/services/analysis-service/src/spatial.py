"""Spatial analysis functions extracted from Surfing-analysis.

Source: surfing_events_detection.py lines 117-282
Functions: IoU calculation, surfer-in-wave detection, wave white level.
These are pure math functions — extracted verbatim with minor cleanup.
"""

import numpy as np


def calc_iou(box_a: list[float], box_b: list[float]) -> float:
    """Calculate intersection over union between two bounding boxes.

    Each box is [x_min, y_min, x_max, y_max].
    Source: surfing_events_detection.py:117-139
    """
    x_a = max(box_a[0], box_b[0])
    y_a = max(box_a[1], box_b[1])
    x_b = min(box_a[2], box_b[2])
    y_b = min(box_a[3], box_b[3])

    inter_area = abs(max(x_b - x_a, 0) * max(y_b - y_a, 0))
    if inter_area == 0:
        return 0.0

    area_a = abs((box_a[2] - box_a[0]) * (box_a[3] - box_a[1]))
    area_b = abs((box_b[2] - box_b[0]) * (box_b[3] - box_b[1]))
    union_area = area_a + area_b - inter_area

    if union_area <= 0:
        return 0.0
    return inter_area / float(union_area)


def find_surfer_wave_association(
    surfer_bboxes: list[list[float]],
    wave_bboxes: list[list[float]],
) -> dict | None:
    """Find the surfer-wave pair with highest IoU overlap.

    Returns dict with surfer_idx, wave_idx, iou — or None if no overlap.
    Source: surfing_events_detection.py:175-198 (get_surfer_insied_wave)
    """
    if not surfer_bboxes or not wave_bboxes:
        return None

    best = None
    for si, s_box in enumerate(surfer_bboxes):
        for wi, w_box in enumerate(wave_bboxes):
            iou = calc_iou(s_box, w_box)
            if best is None or iou > best["iou"]:
                best = {"surfer_idx": si, "wave_idx": wi, "iou": iou}

    if best is not None and best["iou"] > 0:
        return best
    return None


def compute_wave_white_level(bbox: list[float], frame: np.ndarray) -> float:
    """Calculate the wave breaking intensity (whiteness ratio).

    Measures what fraction of pixels in the wave bbox region are 'white'
    (any channel > 150).

    Source: surfing_events_detection.py:269-282 (calculate_wave_wight_level)
    """
    x_min, y_min, x_max, y_max = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])

    # Clamp to frame bounds
    h, w = frame.shape[:2]
    x_min = max(0, x_min)
    y_min = max(0, y_min)
    x_max = min(w, x_max)
    y_max = min(h, y_max)

    region = frame[y_min:y_max, x_min:x_max]
    if region.size == 0:
        return 0.0

    white_count = np.sum(region > 150)
    return round(float(white_count / region.size), 4)


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    """Get center point of a bounding box [x_min, y_min, x_max, y_max]."""
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def bbox_area(bbox: list[float]) -> float:
    """Get area of a bounding box [x_min, y_min, x_max, y_max]."""
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
