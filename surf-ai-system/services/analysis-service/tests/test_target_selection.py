"""Tests for target surfer selection."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.detector import Detection
from src.target_selection import select_target_surfer, TargetSelectionStats


def _det(bbox, conf=0.8):
    return Detection(label="surfer", confidence=conf, bbox=bbox)


def test_single_surfer():
    det = _det([100, 200, 180, 400])
    result = select_target_surfer([det])
    assert result is det


def test_no_surfers():
    result = select_target_surfer([])
    assert result is None


def test_multiple_surfers_no_previous():
    small = _det([100, 200, 120, 220], conf=0.9)   # area 400
    large = _det([100, 200, 300, 500], conf=0.9)   # area 60000
    result = select_target_surfer([small, large])
    assert result is large  # larger area wins


def test_multiple_surfers_with_previous():
    prev = [100, 200, 180, 400]
    nearby = _det([105, 205, 185, 405], conf=0.7)  # close to previous
    far = _det([500, 200, 700, 500], conf=0.9)     # far from previous
    result = select_target_surfer([nearby, far], previous_target_bbox=prev)
    assert result is nearby  # IoU continuity wins


def test_stats_tracking():
    stats = TargetSelectionStats()
    stats.record_frame(0, None)
    stats.record_frame(1, [100, 200, 180, 400])
    stats.record_frame(2, [105, 205, 185, 405])
    stats.record_frame(1, [100, 200, 180, 400])

    d = stats.to_dict()
    assert d["frames_with_zero_surfers"] == 1
    assert d["frames_with_one_surfer"] == 2
    assert d["frames_with_multiple_surfers"] == 1
    assert d["target_surfer_coverage"] > 0.5


if __name__ == "__main__":
    test_single_surfer()
    test_no_surfers()
    test_multiple_surfers_no_previous()
    test_multiple_surfers_with_previous()
    test_stats_tracking()
    print("All target selection tests passed.")
