"""Tests for trajectory analysis."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.trajectory import compute_ride_trajectory, _count_direction_changes


def test_trajectory_rightward_motion():
    centroids = [(100 + i * 10, 300) for i in range(20)]
    timestamps = [i * 100.0 for i in range(20)]
    result = compute_ride_trajectory(centroids, timestamps)
    assert result["dominant_direction"] == "right"
    assert result["distance_px"] > 0
    assert result["avg_speed_px_per_sec"] > 0
    assert result["duration_seconds"] > 0


def test_trajectory_leftward_motion():
    centroids = [(300 - i * 10, 300) for i in range(20)]
    timestamps = [i * 100.0 for i in range(20)]
    result = compute_ride_trajectory(centroids, timestamps)
    assert result["dominant_direction"] == "left"


def test_trajectory_stationary():
    centroids = [(100, 300) for _ in range(20)]
    timestamps = [i * 100.0 for i in range(20)]
    result = compute_ride_trajectory(centroids, timestamps)
    assert result["dominant_direction"] == "unknown"
    assert result["distance_px"] == 0.0


def test_trajectory_too_few_points():
    result = compute_ride_trajectory([(100, 200)], [0.0])
    assert result["dominant_direction"] == "unknown"
    assert result["confidence"] == "low"


def test_direction_changes_simple():
    # Go right then left
    values = list(range(0, 50)) + list(range(50, 0, -1))
    changes = _count_direction_changes(values, window=5)
    assert changes >= 1


def test_direction_changes_monotonic():
    values = list(range(100))
    changes = _count_direction_changes(values, window=5)
    assert changes == 0


if __name__ == "__main__":
    test_trajectory_rightward_motion()
    test_trajectory_leftward_motion()
    test_trajectory_stationary()
    test_trajectory_too_few_points()
    test_direction_changes_simple()
    test_direction_changes_monotonic()
    print("All trajectory tests passed.")
