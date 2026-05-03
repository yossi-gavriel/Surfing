"""Tests for heuristic ride scorer."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scorer import compute_ride_score


def _base_trajectory(**overrides):
    t = {
        "dominant_direction": "right",
        "distance_px": 200.0,
        "avg_speed_px_per_sec": 50.0,
        "duration_seconds": 6.0,
        "direction_changes_x": 2,
        "direction_changes_y": 3,
        "confidence": "high",
    }
    t.update(overrides)
    return t


def _base_wave(**overrides):
    w = {
        "detected": True,
        "coverage_ratio": 0.6,
        "avg_white_level": 0.4,
        "max_white_level": 0.7,
        "avg_surfer_wave_iou": 0.3,
    }
    w.update(overrides)
    return w


def test_score_range():
    """Score is always between 0 and 10."""
    score = compute_ride_score(
        ride_duration_seconds=10.0,
        dominant_direction="right",
        maneuvers=[{"type": "top_turn"}, {"type": "cutback"}, {"type": "bottom_turn"}],
        trajectory=_base_trajectory(),
        wave_analysis=_base_wave(),
        ride_confidence="high",
    )
    assert 0.0 <= score <= 10.0


def test_minimal_ride_low_score():
    """Very short ride with no features scores low."""
    score = compute_ride_score(
        ride_duration_seconds=1.0,
        dominant_direction="unknown",
        maneuvers=[],
        trajectory=_base_trajectory(
            dominant_direction="unknown",
            direction_changes_x=0,
            direction_changes_y=0,
            confidence="low",
        ),
        wave_analysis=None,
        ride_confidence="low",
    )
    assert score < 2.0


def test_good_ride_high_score():
    """Long ride with maneuvers, wave, direction scores well."""
    score = compute_ride_score(
        ride_duration_seconds=12.0,
        dominant_direction="right",
        maneuvers=[
            {"type": "top_turn"},
            {"type": "bottom_turn"},
            {"type": "cutback"},
            {"type": "top_turn"},
        ],
        trajectory=_base_trajectory(direction_changes_x=4, direction_changes_y=5),
        wave_analysis=_base_wave(coverage_ratio=0.8, avg_white_level=0.5),
        ride_confidence="high",
    )
    assert score >= 6.0


def test_duration_affects_score():
    """Longer duration → higher score."""
    short = compute_ride_score(
        ride_duration_seconds=2.0,
        dominant_direction="right",
        maneuvers=[],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    long = compute_ride_score(
        ride_duration_seconds=10.0,
        dominant_direction="right",
        maneuvers=[],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    assert long > short


def test_maneuvers_affect_score():
    """More maneuvers → higher score."""
    no_maneuvers = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    with_maneuvers = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[{"type": "top_turn"}, {"type": "cutback"}],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    assert with_maneuvers > no_maneuvers


def test_maneuver_variety_bonus():
    """Diverse maneuver types score higher than repeated same type."""
    same_type = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[{"type": "top_turn"}, {"type": "top_turn"}],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    diverse = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[{"type": "top_turn"}, {"type": "cutback"}],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    assert diverse > same_type


def test_wave_interaction_bonus():
    """Wave coverage adds to score."""
    no_wave = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    with_wave = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[],
        trajectory=_base_trajectory(),
        wave_analysis=_base_wave(),
    )
    assert with_wave > no_wave


def test_low_confidence_penalty():
    """Low confidence reduces score."""
    high = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[{"type": "top_turn"}],
        trajectory=_base_trajectory(),
        wave_analysis=None,
        ride_confidence="high",
    )
    low = compute_ride_score(
        ride_duration_seconds=6.0,
        dominant_direction="right",
        maneuvers=[{"type": "top_turn"}],
        trajectory=_base_trajectory(),
        wave_analysis=None,
        ride_confidence="low",
    )
    assert high > low


def test_score_is_rounded():
    """Score should be rounded to 1 decimal."""
    score = compute_ride_score(
        ride_duration_seconds=5.0,
        dominant_direction="right",
        maneuvers=[],
        trajectory=_base_trajectory(),
        wave_analysis=None,
    )
    assert score == round(score, 1)


if __name__ == "__main__":
    test_score_range()
    test_minimal_ride_low_score()
    test_good_ride_high_score()
    test_duration_affects_score()
    test_maneuvers_affect_score()
    test_maneuver_variety_bonus()
    test_wave_interaction_bonus()
    test_low_confidence_penalty()
    test_score_is_rounded()
    print("All scorer tests passed.")
