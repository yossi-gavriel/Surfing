"""Frame extraction from video clips using OpenCV.

Supports configurable FPS sampling to avoid processing every frame.
"""

from dataclasses import dataclass
from typing import Generator

import cv2
import numpy as np


@dataclass
class FrameInfo:
    frame_index: int
    timestamp_ms: float
    frame: np.ndarray


@dataclass
class ClipMetadata:
    duration_seconds: float
    total_frames: int
    sampled_frames: int
    native_fps: float
    resolution: tuple[int, int]  # (width, height)
    file_size_bytes: int


def extract_frames(
    video_path: str,
    sample_fps: int = 10,
) -> Generator[FrameInfo, None, None]:
    """Extract frames from a video file at the specified sample rate.

    Args:
        video_path: Path to video file.
        sample_fps: Target frames per second to sample. If the video's native
                     FPS is <= sample_fps, all frames are returned.

    Yields:
        FrameInfo with frame index, timestamp, and numpy array.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file: {video_path}")

    native_fps = cap.get(cv2.CAP_PROP_FPS)
    if native_fps <= 0:
        native_fps = 30.0

    # Calculate frame step for sampling
    frame_step = max(1, int(round(native_fps / sample_fps)))

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_step == 0:
            timestamp_ms = (frame_idx / native_fps) * 1000.0
            yield FrameInfo(
                frame_index=frame_idx,
                timestamp_ms=round(timestamp_ms, 1),
                frame=frame,
            )

        frame_idx += 1

    cap.release()


def get_clip_metadata(video_path: str, sampled_count: int = 0) -> ClipMetadata:
    """Get metadata about a video clip without reading all frames."""
    import os

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video file: {video_path}")

    native_fps = cap.get(cv2.CAP_PROP_FPS)
    if native_fps <= 0:
        native_fps = 30.0

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    duration = total_frames / native_fps if native_fps > 0 else 0.0
    file_size = os.path.getsize(video_path) if os.path.exists(video_path) else 0

    return ClipMetadata(
        duration_seconds=round(duration, 2),
        total_frames=total_frames,
        sampled_frames=sampled_count,
        native_fps=round(native_fps, 2),
        resolution=(width, height),
        file_size_bytes=file_size,
    )
