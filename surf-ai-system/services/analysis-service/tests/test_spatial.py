"""Tests for spatial analysis functions."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from src.spatial import calc_iou, find_surfer_wave_association, compute_wave_white_level, bbox_center, bbox_area


def test_calc_iou_identical_boxes():
    box = [10, 10, 50, 50]
    assert calc_iou(box, box) == 1.0


def test_calc_iou_no_overlap():
    box_a = [0, 0, 10, 10]
    box_b = [20, 20, 30, 30]
    assert calc_iou(box_a, box_b) == 0.0


def test_calc_iou_partial_overlap():
    box_a = [0, 0, 20, 20]
    box_b = [10, 10, 30, 30]
    # Intersection: 10x10 = 100
    # Union: 400 + 400 - 100 = 700
    iou = calc_iou(box_a, box_b)
    assert abs(iou - 100.0 / 700.0) < 0.001


def test_find_surfer_wave_association_single_pair():
    surfers = [[100, 200, 180, 400]]
    waves = [[50, 150, 600, 500]]
    result = find_surfer_wave_association(surfers, waves)
    assert result is not None
    assert result["surfer_idx"] == 0
    assert result["wave_idx"] == 0
    assert result["iou"] > 0


def test_find_surfer_wave_association_no_surfers():
    result = find_surfer_wave_association([], [[10, 10, 100, 100]])
    assert result is None


def test_find_surfer_wave_association_no_overlap():
    surfers = [[0, 0, 10, 10]]
    waves = [[100, 100, 200, 200]]
    result = find_surfer_wave_association(surfers, waves)
    assert result is None


def test_compute_wave_white_level_white_frame():
    frame = np.ones((100, 100, 3), dtype=np.uint8) * 200
    wl = compute_wave_white_level([10, 10, 90, 90], frame)
    assert wl > 0.9


def test_compute_wave_white_level_dark_frame():
    frame = np.ones((100, 100, 3), dtype=np.uint8) * 50
    wl = compute_wave_white_level([10, 10, 90, 90], frame)
    assert wl == 0.0


def test_bbox_center():
    assert bbox_center([10, 20, 30, 40]) == (20.0, 30.0)


def test_bbox_area():
    assert bbox_area([10, 20, 30, 60]) == 800.0
    assert bbox_area([10, 10, 10, 10]) == 0.0


if __name__ == "__main__":
    test_calc_iou_identical_boxes()
    test_calc_iou_no_overlap()
    test_calc_iou_partial_overlap()
    test_find_surfer_wave_association_single_pair()
    test_find_surfer_wave_association_no_surfers()
    test_find_surfer_wave_association_no_overlap()
    test_compute_wave_white_level_white_frame()
    test_compute_wave_white_level_dark_frame()
    test_bbox_center()
    test_bbox_area()
    print("All spatial tests passed.")
