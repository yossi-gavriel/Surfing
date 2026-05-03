"""Tests for maneuver detection."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.maneuvers import ManeuverDetector


def _make_frame(center_x, center_y, bbox_height=200, wave_y_top=None):
    """Helper to build a per_frame_data entry."""
    half_h = bbox_height // 2
    return {
        "frame_index": 0,  # overwritten by caller
        "timestamp_ms": 0.0,
        "surfer_center": (center_x, center_y),
        "target_surfer_bbox": [
            center_x - 40, center_y - half_h,
            center_x + 40, center_y + half_h,
        ],
        "wave_detections": [{"bbox": [0, wave_y_top, 800, 600]}] if wave_y_top is not None else [],
    }


def _build_frames(centers, wave_tops=None, fps=30.0):
    """Build per_frame_data list from (x, y) centers and optional wave tops."""
    frames = []
    for i, (cx, cy) in enumerate(centers):
        wt = wave_tops[i] if wave_tops else None
        f = _make_frame(cx, cy, wave_y_top=wt)
        f["frame_index"] = i
        f["timestamp_ms"] = i * (1000.0 / fps)
        frames.append(f)
    return frames


detector = ManeuverDetector()


# ── top_turn tests ──

def test_top_turn_detected_when_surfer_above_wave():
    """Surfer bbox top goes above wave top for several frames → top_turn."""
    # Wave top at y=300. Surfer center at y=200 with bbox_height=200 → y_top=100 < 300.
    centers = [(400, 400)] * 5 + [(400, 200)] * 8 + [(400, 400)] * 5
    wave_tops = [300] * len(centers)
    frames = _build_frames(centers, wave_tops)

    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    top_turns = [m for m in maneuvers if m["type"] == "top_turn"]
    assert len(top_turns) >= 1, f"Expected top_turn, got {maneuvers}"
    assert top_turns[0]["confidence"] > 0.5


def test_no_top_turn_when_surfer_below_wave():
    """Surfer stays below wave top → no top_turn."""
    centers = [(400, 500)] * 20
    wave_tops = [300] * 20
    frames = _build_frames(centers, wave_tops)

    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    top_turns = [m for m in maneuvers if m["type"] == "top_turn"]
    assert len(top_turns) == 0


def test_top_turn_too_brief_ignored():
    """Surfer above wave for only 1 frame → not enough for top_turn."""
    centers = [(400, 500)] * 10 + [(400, 200)] * 1 + [(400, 500)] * 10
    wave_tops = [300] * len(centers)
    frames = _build_frames(centers, wave_tops)

    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    top_turns = [m for m in maneuvers if m["type"] == "top_turn"]
    assert len(top_turns) == 0


# ── bottom_turn tests ──

def test_bottom_turn_detected_at_y_peak():
    """Surfer moves down then up (Y peak) → bottom_turn."""
    # Y goes from 200 → 500 → 200 (peak at 500 = bottom of wave in image coords)
    n = 30
    centers = []
    for i in range(n):
        if i < n // 2:
            y = 200 + (300 * i / (n // 2))
        else:
            y = 500 - (300 * (i - n // 2) / (n // 2))
        centers.append((400, y))

    frames = _build_frames(centers)
    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    bottom_turns = [m for m in maneuvers if m["type"] == "bottom_turn"]
    assert len(bottom_turns) >= 1, f"Expected bottom_turn, got {maneuvers}"


def test_no_bottom_turn_flat_trajectory():
    """Flat Y trajectory → no bottom_turn."""
    centers = [(400, 300)] * 30
    frames = _build_frames(centers)
    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    bottom_turns = [m for m in maneuvers if m["type"] == "bottom_turn"]
    assert len(bottom_turns) == 0


# ── cutback tests ──

def test_cutback_detected_on_x_reversal():
    """Surfer going right then reverses left → cutback."""
    centers = []
    # Move right for 15 frames
    for i in range(15):
        centers.append((200 + i * 10, 300))
    # Reverse left for 15 frames (sustained reversal)
    for i in range(15):
        centers.append((350 - i * 10, 300))

    frames = _build_frames(centers)
    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    cutbacks = [m for m in maneuvers if m["type"] == "cutback"]
    assert len(cutbacks) >= 1, f"Expected cutback, got {maneuvers}"


def test_no_cutback_same_direction():
    """Surfer moves consistently right → no cutback."""
    centers = [(100 + i * 5, 300) for i in range(30)]
    frames = _build_frames(centers)
    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    cutbacks = [m for m in maneuvers if m["type"] == "cutback"]
    assert len(cutbacks) == 0


def test_no_cutback_unknown_direction():
    """Unknown surfing direction → cutback detection skipped."""
    centers = []
    for i in range(15):
        centers.append((200 + i * 10, 300))
    for i in range(15):
        centers.append((350 - i * 10, 300))

    frames = _build_frames(centers)
    trajectory = {"dominant_direction": "unknown"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    cutbacks = [m for m in maneuvers if m["type"] == "cutback"]
    assert len(cutbacks) == 0


# ── edge cases ──

def test_too_few_frames_returns_empty():
    """Fewer than 10 frames → no maneuvers."""
    centers = [(400, 300)] * 5
    frames = _build_frames(centers)
    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)
    assert maneuvers == []


def test_maneuvers_sorted_by_time():
    """Output maneuvers are sorted by start_time_offset_ms."""
    # Create scenario that triggers multiple maneuver types
    n = 60
    centers = []
    wave_tops = []
    for i in range(n):
        if i < 20:
            centers.append((200 + i * 5, 400))
            wave_tops.append(300)
        elif i < 30:
            # Top turn: surfer goes above wave
            centers.append((300, 150))
            wave_tops.append(300)
        elif i < 45:
            # Move right then reverse for cutback
            centers.append((300 + (i - 30) * 10, 400))
            wave_tops.append(300)
        else:
            centers.append((450 - (i - 45) * 10, 400))
            wave_tops.append(300)

    frames = _build_frames(centers, wave_tops)
    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    for i in range(len(maneuvers) - 1):
        assert maneuvers[i]["start_time_offset_ms"] <= maneuvers[i + 1]["start_time_offset_ms"]


def test_maneuver_has_required_fields():
    """Each maneuver dict has the expected fields."""
    centers = [(400, 400)] * 5 + [(400, 200)] * 8 + [(400, 400)] * 5
    wave_tops = [300] * len(centers)
    frames = _build_frames(centers, wave_tops)

    trajectory = {"dominant_direction": "right"}
    maneuvers = detector.detect_maneuvers(frames, trajectory, None)

    for m in maneuvers:
        assert "type" in m
        assert "start_frame" in m
        assert "end_frame" in m
        assert "start_time_offset_ms" in m
        assert "end_time_offset_ms" in m
        assert "confidence" in m
        assert m["start_time_offset_ms"] <= m["end_time_offset_ms"]


if __name__ == "__main__":
    test_top_turn_detected_when_surfer_above_wave()
    test_no_top_turn_when_surfer_below_wave()
    test_top_turn_too_brief_ignored()
    test_bottom_turn_detected_at_y_peak()
    test_no_bottom_turn_flat_trajectory()
    test_cutback_detected_on_x_reversal()
    test_no_cutback_same_direction()
    test_no_cutback_unknown_direction()
    test_too_few_frames_returns_empty()
    test_maneuvers_sorted_by_time()
    test_maneuver_has_required_fields()
    print("All maneuver tests passed.")
