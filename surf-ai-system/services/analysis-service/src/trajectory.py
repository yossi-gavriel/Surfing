"""Trajectory analysis for ride summary.

Derives ride direction, distance, and average speed from the
surfer's bounding box centroid trajectory across frames.
Uses heuristic direction from trajectory (not CNN body orientation).
"""

import math


def compute_ride_trajectory(
    centroids: list[tuple[float, float]],
    timestamps_ms: list[float],
) -> dict:
    """Compute ride trajectory summary from surfer centroid positions.

    Args:
        centroids: List of (x, y) center positions of target surfer per frame.
        timestamps_ms: Corresponding timestamps in milliseconds.

    Returns:
        Dict with direction, distance, speed, duration, direction_changes.
    """
    if len(centroids) < 2:
        return {
            "dominant_direction": "unknown",
            "distance_px": 0.0,
            "avg_speed_px_per_sec": 0.0,
            "duration_seconds": 0.0,
            "direction_changes_x": 0,
            "direction_changes_y": 0,
            "confidence": "low",
        }

    # Duration
    duration_ms = timestamps_ms[-1] - timestamps_ms[0]
    duration_sec = duration_ms / 1000.0 if duration_ms > 0 else 0.0

    # Total distance (sum of frame-to-frame displacements)
    total_distance = 0.0
    for i in range(1, len(centroids)):
        dx = centroids[i][0] - centroids[i - 1][0]
        dy = centroids[i][1] - centroids[i - 1][1]
        total_distance += math.hypot(dx, dy)

    total_distance = round(total_distance, 1)

    # Average speed
    avg_speed = round(total_distance / duration_sec, 1) if duration_sec > 0 else 0.0

    # Dominant direction: compare median X in first half vs second half
    mid = len(centroids) // 2
    if mid > 0 and mid < len(centroids):
        first_half_x = sorted([c[0] for c in centroids[:mid]])
        second_half_x = sorted([c[0] for c in centroids[mid:]])
        median_first = first_half_x[len(first_half_x) // 2]
        median_second = second_half_x[len(second_half_x) // 2]
        x_displacement = median_second - median_first

        if abs(x_displacement) < 10:
            dominant_direction = "unknown"
        elif x_displacement > 0:
            dominant_direction = "right"
        else:
            dominant_direction = "left"
    else:
        dominant_direction = "unknown"

    # Direction changes in X and Y axes
    direction_changes_x = _count_direction_changes([c[0] for c in centroids], window=5)
    direction_changes_y = _count_direction_changes([c[1] for c in centroids], window=5)

    # Confidence based on data quality
    confidence = "high"
    if len(centroids) < 10:
        confidence = "low"
    elif len(centroids) < 30:
        confidence = "medium"

    return {
        "dominant_direction": dominant_direction,
        "distance_px": total_distance,
        "avg_speed_px_per_sec": avg_speed,
        "duration_seconds": round(duration_sec, 2),
        "direction_changes_x": direction_changes_x,
        "direction_changes_y": direction_changes_y,
        "confidence": confidence,
    }


def _count_direction_changes(values: list[float], window: int = 5) -> int:
    """Count direction changes in a 1D signal using a smoothing window.

    A direction change is when the smoothed signal switches from
    increasing to decreasing or vice versa.
    """
    if len(values) < window * 2:
        return 0

    # Compute smoothed direction: sign of difference of rolling means
    changes = 0
    prev_direction = 0

    for i in range(window, len(values) - window):
        left_mean = sum(values[i - window:i]) / window
        right_mean = sum(values[i:i + window]) / window
        diff = right_mean - left_mean

        if abs(diff) < 3.0:
            direction = 0
        elif diff > 0:
            direction = 1
        else:
            direction = -1

        if prev_direction != 0 and direction != 0 and direction != prev_direction:
            changes += 1

        if direction != 0:
            prev_direction = direction

    return changes
