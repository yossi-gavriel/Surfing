"""Heuristic ride scorer — produces a 0-10 ride quality score.

Derived from the feature set used by LearningObj (Surfing-analysis
python/learning_from_analysis/LearningObj.py) which trains regressors on:
  - direction counts (left, right, up, down)
  - maneuver counts (exercises/top_turns, pipes)
  - falls
  - ride duration (frames_count / normal_factor)

This module replaces the trained regressor with a deterministic weighted
heuristic built from the same feature categories, so no trained model
weights are required.  The score can be refined later by swapping in a
trained model without changing the interface.
"""

from __future__ import annotations


def compute_ride_score(
    ride_duration_seconds: float,
    dominant_direction: str,
    maneuvers: list[dict],
    trajectory: dict,
    wave_analysis: dict | None,
    ride_confidence: str = "medium",
) -> float:
    """Compute a heuristic ride score on a 0-10 scale.

    Args:
        ride_duration_seconds: Duration of the ride in seconds.
        dominant_direction: "left", "right", or "unknown".
        maneuvers: List of maneuver dicts from ManeuverDetector.
        trajectory: Trajectory dict from compute_ride_trajectory.
        wave_analysis: Wave analysis dict or None.
        ride_confidence: "high", "medium", or "low".

    Returns:
        Float score clamped to [0.0, 10.0], rounded to 1 decimal.
    """
    score = 0.0

    # --- Duration component (max 3.0 pts) ---
    # Longer rides are better. 2s = minimal, 8s+ = full credit.
    if ride_duration_seconds >= 8.0:
        score += 3.0
    elif ride_duration_seconds >= 2.0:
        score += 3.0 * (ride_duration_seconds - 2.0) / 6.0
    # < 2s gets 0

    # --- Direction component (max 1.5 pts) ---
    # Having a clear surfing direction indicates a real ride.
    if dominant_direction in ("left", "right"):
        score += 1.0
        # Bonus for high-confidence direction
        if trajectory.get("confidence") == "high":
            score += 0.5

    # --- Maneuver component (max 3.0 pts) ---
    # Each maneuver type contributes, with diminishing returns.
    maneuver_types = {}
    for m in maneuvers:
        mt = m.get("type", "unknown")
        maneuver_types[mt] = maneuver_types.get(mt, 0) + 1

    # Variety bonus: more distinct types = better
    variety_pts = min(1.5, len(maneuver_types) * 0.5)
    score += variety_pts

    # Count bonus: more maneuvers = more active ride (diminishing)
    total_maneuvers = len(maneuvers)
    count_pts = min(1.5, total_maneuvers * 0.3)
    score += count_pts

    # --- Wave interaction component (max 1.5 pts) ---
    if wave_analysis and wave_analysis.get("detected"):
        coverage = wave_analysis.get("coverage_ratio", 0.0)
        # Surfer-in-wave coverage: higher = better wave usage
        score += min(1.0, coverage * 2.0)

        # White water level bonus (active wave)
        avg_white = wave_analysis.get("avg_white_level", 0.0)
        if avg_white > 0.3:
            score += 0.5

    # --- Movement quality component (max 1.0 pts) ---
    # Direction changes indicate active surfing vs just riding straight.
    dir_changes_x = trajectory.get("direction_changes_x", 0)
    dir_changes_y = trajectory.get("direction_changes_y", 0)
    total_changes = dir_changes_x + dir_changes_y
    score += min(1.0, total_changes * 0.15)

    # --- Confidence penalty ---
    if ride_confidence == "low":
        score *= 0.7
    elif ride_confidence == "medium":
        score *= 0.9

    return round(max(0.0, min(10.0, score)), 1)
