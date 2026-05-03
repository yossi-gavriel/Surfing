"""Lightweight target-surfer selection for multi-surfer frames.

Clips may contain multiple surfers. This module selects which surfer
to analyze in each frame using IoU continuity with the previous frame
and bbox area * confidence as tiebreaker.

This is NOT a full tracker — it's a per-frame greedy selection.
"""

from src.detector import Detection
from src.spatial import calc_iou, bbox_area


def select_target_surfer(
    surfer_detections: list[Detection],
    previous_target_bbox: list[float] | None = None,
) -> Detection | None:
    """Select the target surfer from a list of surfer detections.

    Algorithm:
        1. If exactly 1 surfer → return it
        2. If 0 surfers → return None
        3. If multiple surfers:
           a. Score each by bbox_area * confidence
           b. If previous target exists, prefer surfer with highest IoU
              to previous (temporal consistency)
           c. Tiebreak by area * confidence

    Returns the selected Detection or None.
    """
    if not surfer_detections:
        return None

    if len(surfer_detections) == 1:
        return surfer_detections[0]

    # Multiple surfers — score each
    scored = []
    for det in surfer_detections:
        area_score = bbox_area(det.bbox) * det.confidence
        iou_score = 0.0
        if previous_target_bbox is not None:
            iou_score = calc_iou(det.bbox, previous_target_bbox)
        scored.append((det, iou_score, area_score))

    # Sort by IoU (temporal consistency) first, then area*confidence
    scored.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return scored[0][0]


class TargetSelectionStats:
    """Tracks statistics about target selection across all frames."""

    def __init__(self):
        self.frames_with_zero_surfers = 0
        self.frames_with_one_surfer = 0
        self.frames_with_multiple_surfers = 0
        self.target_surfer_switches = 0
        self._previous_target_bbox: list[float] | None = None
        self._total_frames = 0

    def record_frame(
        self,
        surfer_count: int,
        selected_bbox: list[float] | None,
    ) -> None:
        self._total_frames += 1

        if surfer_count == 0:
            self.frames_with_zero_surfers += 1
        elif surfer_count == 1:
            self.frames_with_one_surfer += 1
        else:
            self.frames_with_multiple_surfers += 1

        # Detect target switches (IoU < 0.3 with previous)
        if selected_bbox is not None and self._previous_target_bbox is not None:
            iou = calc_iou(selected_bbox, self._previous_target_bbox)
            if iou < 0.3:
                self.target_surfer_switches += 1

        if selected_bbox is not None:
            self._previous_target_bbox = selected_bbox

    @property
    def target_surfer_coverage(self) -> float:
        frames_with_target = self.frames_with_one_surfer + self.frames_with_multiple_surfers
        if self._total_frames == 0:
            return 0.0
        return round(frames_with_target / self._total_frames, 4)

    def to_dict(self) -> dict:
        return {
            "frames_with_zero_surfers": self.frames_with_zero_surfers,
            "frames_with_one_surfer": self.frames_with_one_surfer,
            "frames_with_multiple_surfers": self.frames_with_multiple_surfers,
            "target_surfer_switches": self.target_surfer_switches,
            "target_surfer_coverage": self.target_surfer_coverage,
        }
