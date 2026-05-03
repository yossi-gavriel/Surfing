"""Limited maneuver detection: bottom_turn, top_turn, cutback.

Extracted and rewritten from Surfing-analysis surfing_events_detection.py.
Sources:
  - top_turn: is_excercise_func (lines 608-644) — surfer bbox above wave top
  - bottom_turn: get_direction_analysis_smooth Y-axis (lines 441-530) — Y reversal at bottom
  - cutback: get_direction_analysis_smooth X-axis (lines 441-530) — X reversal against surfing direction

All functions operate on the per_frame_data produced by the analyzer,
not on raw video frames.
"""


class ManeuverDetector:
    """Detects maneuvers from per-frame spatial and detection data.

    Designed to be stateless — all state is passed in per call.
    """

    def __init__(self, logger=None):
        self.logger = logger

    def detect_maneuvers(
        self,
        per_frame_data: list[dict],
        trajectory: dict,
        wave_analysis: dict | None,
        clip_fps: float = 30.0,
    ) -> list[dict]:
        """Detect maneuvers from analyzed frame data.

        Args:
            per_frame_data: List of per-frame dicts from analyzer (with
                target_surfer_bbox, wave_detections, surfer_center, etc.)
            trajectory: Trajectory dict (dominant_direction, etc.)
            wave_analysis: Wave analysis dict or None.
            clip_fps: Native FPS of the clip (for time offset calculation).

        Returns:
            List of maneuver dicts: {type, start_time_offset_ms, end_time_offset_ms, confidence}
        """
        maneuvers = []

        # Extract surfer and wave position series for temporal analysis
        surfer_centers = []
        wave_tops = []
        frame_indices = []
        timestamps_ms = []

        for frame in per_frame_data:
            center = frame.get("surfer_center")
            bbox = frame.get("target_surfer_bbox")
            waves = frame.get("wave_detections", [])

            if center is None or bbox is None:
                surfer_centers.append(None)
                wave_tops.append(None)
            else:
                surfer_centers.append({
                    "x": center[0],
                    "y": center[1],
                    "y_bottom": bbox[3],
                    "y_top": bbox[1],
                })
                if waves:
                    # Use wave with best overlap (first wave in list)
                    wave_tops.append(waves[0]["bbox"][1])  # y_min of wave = top
                else:
                    wave_tops.append(None)

            frame_indices.append(frame.get("frame_index", 0))
            timestamps_ms.append(frame.get("timestamp_ms", 0.0))

        if len(surfer_centers) < 10:
            return maneuvers

        # Detect each maneuver type
        top_turns = self._detect_top_turns(surfer_centers, wave_tops, frame_indices, timestamps_ms)
        bottom_turns = self._detect_bottom_turns(surfer_centers, frame_indices, timestamps_ms)

        surfing_direction = trajectory.get("dominant_direction", "unknown")
        cutbacks = self._detect_cutbacks(surfer_centers, frame_indices, timestamps_ms, surfing_direction)

        maneuvers.extend(top_turns)
        maneuvers.extend(bottom_turns)
        maneuvers.extend(cutbacks)

        # Sort by start time
        maneuvers.sort(key=lambda m: m.get("start_time_offset_ms", 0))
        return maneuvers

    def _detect_top_turns(
        self,
        surfer_centers: list[dict | None],
        wave_tops: list[float | None],
        frame_indices: list[int],
        timestamps_ms: list[float],
    ) -> list[dict]:
        """Detect top turns: surfer bbox top goes above wave top edge.

        Source: is_excercise_func (surfing_events_detection.py:608-644)
        Original logic: surfer_max_y < wave_min_y for > threshold consecutive frames.
        Adapted: surfer y_top < wave y_top (surfer rises above wave crest).
        """
        MIN_FRAMES = 3
        MAX_GAP = 3
        maneuvers = []

        in_event = False
        event_start_idx = 0
        consecutive = 0
        gap = 0

        for i in range(len(surfer_centers)):
            sc = surfer_centers[i]
            wt = wave_tops[i]

            if sc is None or wt is None:
                if in_event:
                    gap += 1
                    if gap > MAX_GAP:
                        if consecutive >= MIN_FRAMES:
                            maneuvers.append(self._make_maneuver(
                                "top_turn", event_start_idx, i - gap,
                                frame_indices, timestamps_ms,
                                confidence=min(0.9, 0.5 + consecutive * 0.05),
                            ))
                        in_event = False
                        consecutive = 0
                        gap = 0
                continue

            surfer_above_wave = sc["y_top"] < wt

            if surfer_above_wave:
                if not in_event:
                    in_event = True
                    event_start_idx = i
                    consecutive = 1
                    gap = 0
                else:
                    consecutive += 1
                    gap = 0
            else:
                if in_event:
                    gap += 1
                    if gap > MAX_GAP:
                        if consecutive >= MIN_FRAMES:
                            maneuvers.append(self._make_maneuver(
                                "top_turn", event_start_idx, i - gap,
                                frame_indices, timestamps_ms,
                                confidence=min(0.9, 0.5 + consecutive * 0.05),
                            ))
                        in_event = False
                        consecutive = 0
                        gap = 0

        # Handle event extending to end
        if in_event and consecutive >= MIN_FRAMES:
            maneuvers.append(self._make_maneuver(
                "top_turn", event_start_idx, len(surfer_centers) - 1,
                frame_indices, timestamps_ms,
                confidence=min(0.9, 0.5 + consecutive * 0.05),
            ))

        return maneuvers

    def _detect_bottom_turns(
        self,
        surfer_centers: list[dict | None],
        frame_indices: list[int],
        timestamps_ms: list[float],
    ) -> list[dict]:
        """Detect bottom turns: Y-axis direction reversal (downward → upward).

        Source: get_direction_analysis_smooth Y-axis (surfing_events_detection.py:441-530)
        Simplified: look for valleys in the surfer Y position trajectory.
        A bottom turn = surfer moves downward then reverses upward.
        """
        WINDOW = 5
        MIN_DISPLACEMENT = 15  # pixels

        y_positions = []
        valid_indices = []
        for i, sc in enumerate(surfer_centers):
            if sc is not None:
                y_positions.append(sc["y"])
                valid_indices.append(i)

        if len(y_positions) < WINDOW * 3:
            return []

        maneuvers = []
        # Find valleys (local minima in smoothed Y — note: Y increases downward in image coords)
        # Bottom turn = surfer goes DOWN in frame (Y increases) then comes back UP (Y decreases)
        # So we look for peaks in Y (maxima), which represent the bottom of the wave
        for i in range(WINDOW, len(y_positions) - WINDOW):
            left_mean = sum(y_positions[i - WINDOW:i]) / WINDOW
            right_mean = sum(y_positions[i:i + WINDOW]) / WINDOW
            center_val = y_positions[i]

            # Peak in Y = bottom of wave: value higher than both sides
            if center_val > left_mean + MIN_DISPLACEMENT and center_val > right_mean + MIN_DISPLACEMENT:
                orig_idx = valid_indices[i]
                start_idx = valid_indices[max(0, i - WINDOW)]
                end_idx = valid_indices[min(len(valid_indices) - 1, i + WINDOW)]

                # Check we haven't already reported a nearby bottom_turn
                if maneuvers and abs(timestamps_ms[orig_idx] - maneuvers[-1].get("start_time_offset_ms", 0)) < 500:
                    continue

                displacement = min(center_val - left_mean, center_val - right_mean)
                confidence = min(0.85, 0.4 + displacement / 100.0)

                maneuvers.append(self._make_maneuver(
                    "bottom_turn", start_idx, end_idx,
                    frame_indices, timestamps_ms,
                    confidence=round(confidence, 2),
                ))

        return maneuvers

    def _detect_cutbacks(
        self,
        surfer_centers: list[dict | None],
        frame_indices: list[int],
        timestamps_ms: list[float],
        surfing_direction: str,
    ) -> list[dict]:
        """Detect cutbacks: sustained X-axis reversal against surfing direction.

        Source: get_direction_analysis_smooth X-axis (surfing_events_detection.py:441-530)
        + is_turning_func (lines 713-722)
        A cutback = surfer reverses horizontal direction back toward the breaking part
        of the wave (opposite to surfing direction) for a sustained period.
        """
        if surfing_direction not in ("left", "right"):
            return []

        WINDOW = 5
        MIN_REVERSAL_FRAMES = 8
        MIN_DISPLACEMENT = 20  # pixels

        x_positions = []
        valid_indices = []
        for i, sc in enumerate(surfer_centers):
            if sc is not None:
                x_positions.append(sc["x"])
                valid_indices.append(i)

        if len(x_positions) < WINDOW * 3:
            return []

        # Compute smoothed X-direction: +1 = rightward, -1 = leftward
        directions = []
        for i in range(WINDOW, len(x_positions)):
            diff = x_positions[i] - x_positions[i - WINDOW]
            if abs(diff) < 3:
                directions.append(0)
            elif diff > 0:
                directions.append(1)
            else:
                directions.append(-1)

        # Cutback direction: opposite to surfing direction
        cutback_dir = -1 if surfing_direction == "right" else 1

        maneuvers = []
        in_reversal = False
        reversal_start = 0
        reversal_frames = 0

        for i, d in enumerate(directions):
            if d == cutback_dir:
                if not in_reversal:
                    in_reversal = True
                    reversal_start = i + WINDOW  # offset by window
                    reversal_frames = 1
                else:
                    reversal_frames += 1
            else:
                if in_reversal and reversal_frames >= MIN_REVERSAL_FRAMES:
                    start_vi = valid_indices[reversal_start] if reversal_start < len(valid_indices) else 0
                    end_vi = valid_indices[min(len(valid_indices) - 1, reversal_start + reversal_frames)]

                    # Check displacement
                    x_start = x_positions[reversal_start] if reversal_start < len(x_positions) else 0
                    x_end = x_positions[min(len(x_positions) - 1, reversal_start + reversal_frames)]
                    displacement = abs(x_end - x_start)

                    if displacement >= MIN_DISPLACEMENT:
                        confidence = min(0.85, 0.4 + reversal_frames / 30.0 + displacement / 200.0)
                        direction_label = "right" if cutback_dir == 1 else "left"
                        maneuvers.append(self._make_maneuver(
                            "cutback", start_vi, end_vi,
                            frame_indices, timestamps_ms,
                            confidence=round(confidence, 2),
                            extra={"direction": direction_label},
                        ))

                in_reversal = False
                reversal_frames = 0

        # Handle reversal extending to end
        if in_reversal and reversal_frames >= MIN_REVERSAL_FRAMES:
            start_vi = valid_indices[reversal_start] if reversal_start < len(valid_indices) else 0
            end_vi = valid_indices[min(len(valid_indices) - 1, reversal_start + reversal_frames)]
            x_start = x_positions[reversal_start] if reversal_start < len(x_positions) else 0
            x_end = x_positions[min(len(x_positions) - 1, reversal_start + reversal_frames)]
            displacement = abs(x_end - x_start)

            if displacement >= MIN_DISPLACEMENT:
                confidence = min(0.85, 0.4 + reversal_frames / 30.0 + displacement / 200.0)
                direction_label = "right" if cutback_dir == 1 else "left"
                maneuvers.append(self._make_maneuver(
                    "cutback", start_vi, end_vi,
                    frame_indices, timestamps_ms,
                    confidence=round(confidence, 2),
                    extra={"direction": direction_label},
                ))

        return maneuvers

    def _make_maneuver(
        self,
        maneuver_type: str,
        start_idx: int,
        end_idx: int,
        frame_indices: list[int],
        timestamps_ms: list[float],
        confidence: float = 0.5,
        extra: dict | None = None,
    ) -> dict:
        """Create a maneuver record with frame indices and time offsets."""
        start_idx = max(0, min(start_idx, len(frame_indices) - 1))
        end_idx = max(0, min(end_idx, len(timestamps_ms) - 1))

        m = {
            "type": maneuver_type,
            "start_frame": frame_indices[start_idx],
            "end_frame": frame_indices[end_idx],
            "start_time_offset_ms": round(timestamps_ms[start_idx]),
            "end_time_offset_ms": round(timestamps_ms[end_idx]),
            "confidence": confidence,
        }
        if extra:
            m.update(extra)
        return m
