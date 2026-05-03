"""Ride analyzer — orchestrates the full analysis pipeline.

Downloads clip from S3, extracts frames, runs detection, performs spatial
and trajectory analysis, writes canonical and debug artifacts to S3.
"""

import json
import os
import tempfile
import time

from src.config import AnalysisConfig, MODEL_VERSION
from src.detector import WaveSurferDetector, Detection
from src.frame_loader import extract_frames, get_clip_metadata
from src.spatial import find_surfer_wave_association, compute_wave_white_level, bbox_center
from src.target_selection import select_target_surfer, TargetSelectionStats
from src.trajectory import compute_ride_trajectory
from src.scorer import compute_ride_score


class RideAnalyzer:
    """Main analysis pipeline. Created once, reused across messages."""

    def __init__(self, config: AnalysisConfig, s3_client, logger):
        self.config = config
        self.s3_client = s3_client
        self.logger = logger
        self._detector = None
        self._maneuver_detector = None

    def _get_detector(self) -> WaveSurferDetector:
        if self._detector is None:
            self._detector = WaveSurferDetector(
                model_path=self.config.yolo_model_path,
                device="cpu",
                logger=self.logger,
            )
        return self._detector

    def _get_maneuver_detector(self):
        """Lazy-load maneuver detector (Phase 2+)."""
        if self._maneuver_detector is None:
            try:
                from src.maneuvers import ManeuverDetector
                self._maneuver_detector = ManeuverDetector(self.logger)
                self.logger.info("ManeuverDetector loaded for analysis")
            except ImportError:
                self._maneuver_detector = False  # sentinel: not available
        return self._maneuver_detector if self._maneuver_detector is not False else None

    def analyze(self, msg_body: dict) -> dict:
        """Run the full analysis pipeline on a clip.

        Returns dict with:
            - canonical_s3: S3 path to ride_summary.json
            - debug_s3: S3 path to debug_analysis.json (if enabled)
            - ride_duration_seconds, dominant_direction, maneuver_count, ride_score
            - failure_code, failure_reason (if failed)
        """
        track_id = msg_body.get("track_id", "unknown")
        clip_s3 = msg_body.get("clip_s3", "")
        timing = {}

        # --- Stage 1: Download clip ---
        t0 = time.time()
        self.logger.info("[%s] stage=clip_download status=started clip_s3=%s", track_id, clip_s3)

        local_path = None
        try:
            local_path = self._download_clip(clip_s3)
            timing["clip_download_ms"] = int((time.time() - t0) * 1000)
            self.logger.info(
                "[%s] stage=clip_download status=completed duration_ms=%d",
                track_id, timing["clip_download_ms"],
            )
        except Exception as e:
            self.logger.error("[%s] stage=clip_download status=failed error=%s", track_id, e)
            return {"failure_code": "clip_download_failed", "failure_reason": str(e)}

        try:
            return self._analyze_local(local_path, msg_body, timing)
        finally:
            if local_path and os.path.exists(local_path):
                os.remove(local_path)

    def _download_clip(self, clip_s3: str) -> str:
        """Download clip from S3 to a local temp file. Returns local path."""
        if "://" in clip_s3:
            parts = clip_s3.split("//", 1)[1].split("/", 1)
            bucket = parts[0]
            key = parts[1]
        else:
            bucket = self.config.s3_bucket
            key = clip_s3

        suffix = ".mp4" if key.endswith(".mp4") else ".ts"
        fd, local_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)

        self.s3_client.download_file(bucket, key, local_path)

        file_size = os.path.getsize(local_path)
        if file_size == 0:
            os.remove(local_path)
            raise ValueError("Downloaded clip is 0 bytes")

        return local_path

    def _analyze_local(self, local_path: str, msg_body: dict, timing: dict) -> dict:
        """Run analysis on a local video file."""
        track_id = msg_body.get("track_id", "unknown")

        # --- Stage 2: Extract frames ---
        t0 = time.time()
        self.logger.info(
            "[%s] stage=frame_extraction status=started sample_fps=%d",
            track_id, self.config.default_sample_fps,
        )

        frames = []
        for frame_info in extract_frames(local_path, sample_fps=self.config.default_sample_fps):
            frames.append(frame_info)

        timing["frame_extraction_ms"] = int((time.time() - t0) * 1000)

        if len(frames) < 3:
            return {"failure_code": "clip_too_short", "failure_reason": f"Only {len(frames)} frames extracted"}

        clip_meta = get_clip_metadata(local_path, sampled_count=len(frames))
        self.logger.info(
            "[%s] stage=frame_extraction status=completed duration_ms=%d frames_sampled=%d total_frames=%d",
            track_id, timing["frame_extraction_ms"], len(frames), clip_meta.total_frames,
        )

        # --- Stage 3: Detection ---
        t0 = time.time()
        detector = self._get_detector()
        self.logger.info(
            "[%s] stage=detection status=started model=%s",
            track_id, MODEL_VERSION,
        )

        per_frame_data = []
        target_stats = TargetSelectionStats()
        centroids = []
        timestamps_ms = []
        surfer_wave_ious = []
        wave_white_levels = []
        frames_with_surfer = 0
        frames_with_wave = 0
        frames_with_surfer_in_wave = 0
        previous_target_bbox = None

        for frame_info in frames:
            detections = detector.detect(
                frame_info.frame,
                surfer_confidence=self.config.default_surfer_confidence,
                wave_confidence=self.config.default_wave_confidence,
            )

            surfer_dets = [d for d in detections if d.label == "surfer"]
            wave_dets = [d for d in detections if d.label == "wave"]

            # Target surfer selection
            target = select_target_surfer(surfer_dets, previous_target_bbox)
            target_stats.record_frame(len(surfer_dets), target.bbox if target else None)

            frame_record = {
                "frame_index": frame_info.frame_index,
                "timestamp_ms": frame_info.timestamp_ms,
                "surfer_detections": [
                    {"bbox": d.bbox, "confidence": d.confidence, "is_target": (target is not None and d.bbox == target.bbox)}
                    for d in surfer_dets
                ],
                "wave_detections": [
                    {"bbox": d.bbox, "confidence": d.confidence}
                    for d in wave_dets
                ],
                "target_surfer_bbox": target.bbox if target else None,
                "surfer_center": None,
                "surfer_in_wave_iou": None,
                "wave_white_level": None,
            }

            if target:
                frames_with_surfer += 1
                center = bbox_center(target.bbox)
                frame_record["surfer_center"] = [round(center[0], 1), round(center[1], 1)]
                centroids.append(center)
                timestamps_ms.append(frame_info.timestamp_ms)
                previous_target_bbox = target.bbox

                if wave_dets:
                    frames_with_wave += 1
                    assoc = find_surfer_wave_association([target.bbox], [d.bbox for d in wave_dets])
                    if assoc and assoc["iou"] > 0:
                        frames_with_surfer_in_wave += 1
                        surfer_wave_ious.append(assoc["iou"])
                        frame_record["surfer_in_wave_iou"] = round(assoc["iou"], 4)

                    # Wave white level from best-matching wave
                    best_wave_idx = assoc["wave_idx"] if assoc else 0
                    if best_wave_idx < len(wave_dets):
                        wl = compute_wave_white_level(wave_dets[best_wave_idx].bbox, frame_info.frame)
                        wave_white_levels.append(wl)
                        frame_record["wave_white_level"] = wl
            elif wave_dets:
                frames_with_wave += 1

            per_frame_data.append(frame_record)

        timing["detection_ms"] = int((time.time() - t0) * 1000)

        self.logger.info(
            "[%s] stage=detection status=completed duration_ms=%d frames_sampled=%d surfer_detections=%d wave_detections=%d",
            track_id, timing["detection_ms"], len(frames), frames_with_surfer, frames_with_wave,
        )

        # Check for no surfer
        if frames_with_surfer == 0:
            return {"failure_code": "no_surfer_detected", "failure_reason": "Zero frames with surfer detection"}

        # --- Stage 4: Spatial analysis ---
        t0 = time.time()
        sampled = len(frames)
        wave_coverage_ratio = round(frames_with_surfer_in_wave / sampled, 4) if sampled > 0 else 0.0
        avg_wave_white_level = round(sum(wave_white_levels) / len(wave_white_levels), 4) if wave_white_levels else 0.0
        max_wave_white_level = round(max(wave_white_levels), 4) if wave_white_levels else 0.0
        avg_surfer_wave_iou = round(sum(surfer_wave_ious) / len(surfer_wave_ious), 4) if surfer_wave_ious else 0.0

        wave_detected = frames_with_wave > 0
        wave_analysis = {
            "detected": wave_detected,
            "coverage_ratio": wave_coverage_ratio,
            "avg_white_level": avg_wave_white_level,
            "max_white_level": max_wave_white_level,
            "avg_surfer_wave_iou": avg_surfer_wave_iou,
        } if wave_detected else None

        timing["spatial_analysis_ms"] = int((time.time() - t0) * 1000)

        self.logger.info(
            "[%s] stage=spatial_analysis status=completed wave_coverage=%.3f avg_white_level=%.3f",
            track_id, wave_coverage_ratio, avg_wave_white_level,
        )

        # --- Stage 5: Trajectory analysis ---
        t0 = time.time()
        trajectory = compute_ride_trajectory(centroids, timestamps_ms)
        timing["trajectory_ms"] = int((time.time() - t0) * 1000)

        self.logger.info(
            "[%s] stage=trajectory status=completed dominant_direction=%s direction_changes_x=%d",
            track_id, trajectory["dominant_direction"], trajectory["direction_changes_x"],
        )

        # Ride duration from message timestamps (more accurate than frame count)
        start_time = msg_body.get("start_time", "")
        end_time = msg_body.get("end_time", "")
        ride_duration = trajectory["duration_seconds"]
        if start_time and end_time:
            try:
                from datetime import datetime
                st = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
                et = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
                ride_duration = round((et - st).total_seconds(), 2)
            except Exception:
                pass

        # Ride confidence
        ride_confidence = trajectory["confidence"]
        ts = target_stats
        if ts.target_surfer_switches > 3 or ts.target_surfer_coverage < 0.7:
            ride_confidence = "low"

        # --- Stage 6: Maneuver detection (Phase 2+) ---
        maneuvers = []
        maneuver_detector = self._get_maneuver_detector()
        if maneuver_detector is not None:
            t0 = time.time()
            try:
                maneuvers = maneuver_detector.detect_maneuvers(
                    per_frame_data=per_frame_data,
                    trajectory=trajectory,
                    wave_analysis=wave_analysis,
                    clip_fps=clip_meta.native_fps,
                )
                timing["maneuver_ms"] = int((time.time() - t0) * 1000)
                self.logger.info(
                    "[%s] stage=maneuver_detection status=completed maneuvers_found=%d duration_ms=%d",
                    track_id, len(maneuvers), timing["maneuver_ms"],
                )
            except Exception as e:
                self.logger.warning("[%s] Maneuver detection failed (non-blocking): %s", track_id, e)
                timing["maneuver_ms"] = int((time.time() - t0) * 1000)

        # --- Stage 7: Scoring ---
        t0 = time.time()
        ride_score = compute_ride_score(
            ride_duration_seconds=ride_duration,
            dominant_direction=trajectory["dominant_direction"],
            maneuvers=maneuvers,
            trajectory=trajectory,
            wave_analysis=wave_analysis,
            ride_confidence=ride_confidence,
        )
        timing["scoring_ms"] = int((time.time() - t0) * 1000)
        self.logger.info("[%s] stage=scoring status=completed ride_score=%.1f", track_id, ride_score)

        # --- Stage 8: Build canonical output ---
        canonical = {
            "$schema": "ride_summary_v1",
            "track_id": track_id,
            "video_id": msg_body.get("video_id"),
            "user_id": msg_body.get("user_id"),
            "pool_id": msg_body.get("pool_id"),
            "model_version": MODEL_VERSION,
            "status": "completed",
            "ride": {
                "duration_seconds": ride_duration,
                "dominant_direction": trajectory["dominant_direction"],
                "distance_px": trajectory["distance_px"],
                "avg_speed_px_per_sec": trajectory["avg_speed_px_per_sec"],
                "start_time": start_time,
                "end_time": end_time,
                "confidence": ride_confidence,
            },
            "wave": wave_analysis,
            "maneuvers": [
                {
                    "type": m["type"],
                    "start_time_offset_ms": m.get("start_time_offset_ms"),
                    "end_time_offset_ms": m.get("end_time_offset_ms"),
                    "confidence": m.get("confidence"),
                }
                for m in maneuvers
            ] if maneuvers else [],
            "score": ride_score,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        }

        # --- Stage 9: Build debug output ---
        debug = {
            "track_id": track_id,
            "model_version": MODEL_VERSION,
            "config_snapshot": {
                "sample_fps": self.config.default_sample_fps,
                "surfer_confidence_threshold": self.config.default_surfer_confidence,
                "wave_confidence_threshold": self.config.default_wave_confidence,
            },
            "clip_metadata": {
                "duration_seconds": clip_meta.duration_seconds,
                "total_frames": clip_meta.total_frames,
                "sampled_frames": clip_meta.sampled_frames,
                "native_fps": clip_meta.native_fps,
                "resolution": list(clip_meta.resolution),
                "file_size_bytes": clip_meta.file_size_bytes,
            },
            "target_selection": target_stats.to_dict(),
            "detection_summary": {
                "frames_with_surfer": frames_with_surfer,
                "frames_with_wave": frames_with_wave,
                "frames_with_surfer_in_wave": frames_with_surfer_in_wave,
                "surfer_detection_rate": round(frames_with_surfer / sampled, 4) if sampled else 0,
                "wave_detection_rate": round(frames_with_wave / sampled, 4) if sampled else 0,
            },
            "trajectory": {
                "x_centers": [round(c[0], 1) for c in centroids],
                "y_bottoms": [round(c[1], 1) for c in centroids],
                "direction_changes_x": trajectory["direction_changes_x"],
                "direction_changes_y": trajectory["direction_changes_y"],
            },
            "per_frame": per_frame_data,
            "processing_timing": timing,
        }

        # --- Stage 10: Write artifacts to S3 ---
        t0 = time.time()
        self.logger.info("[%s] stage=artifact_write status=started", track_id)

        canonical_key = f"analysis/{track_id}/ride_summary.json"
        canonical_s3 = None
        debug_s3 = None

        try:
            self.s3_client.put_object(
                Bucket=self.config.s3_bucket,
                Key=canonical_key,
                Body=json.dumps(canonical, indent=2),
                ContentType="application/json",
            )
            canonical_s3 = f"s3://{self.config.s3_bucket}/{canonical_key}"
        except Exception as e:
            self.logger.error("[%s] Failed to write canonical artifact: %s", track_id, e)
            return {"failure_code": "s3_write_failed", "failure_reason": f"Canonical write failed: {e}"}

        if self.config.debug_artifacts_enabled:
            debug_key = f"analysis/{track_id}/debug_analysis.json"
            try:
                self.s3_client.put_object(
                    Bucket=self.config.s3_bucket,
                    Key=debug_key,
                    Body=json.dumps(debug, indent=2, default=str),
                    ContentType="application/json",
                )
                debug_s3 = f"s3://{self.config.s3_bucket}/{debug_key}"
            except Exception as e:
                self.logger.warning("[%s] Failed to write debug artifact (non-blocking): %s", track_id, e)

        timing["artifact_write_ms"] = int((time.time() - t0) * 1000)
        timing["total_ms"] = sum(v for v in timing.values() if isinstance(v, int))

        self.logger.info(
            "[%s] stage=artifact_write status=completed canonical_s3=%s debug_s3=%s duration_ms=%d",
            track_id, canonical_s3, debug_s3, timing["artifact_write_ms"],
        )

        return {
            "canonical_s3": canonical_s3,
            "debug_s3": debug_s3,
            "ride_duration_seconds": ride_duration,
            "dominant_direction": trajectory["dominant_direction"],
            "maneuver_count": len(maneuvers),
            "ride_score": ride_score,
        }
