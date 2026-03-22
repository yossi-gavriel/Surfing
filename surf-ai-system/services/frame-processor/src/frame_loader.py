import cv2
import os
from typing import Generator, Tuple

def extract_frames(video_path: str, sample_rate: int = 5) -> Generator[Tuple[int, float, object], None, None]:
    """
    Extracts frames from video.
    Yields (frame_idx, timestamp_sec, frame_bgr_array)
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0 # fallback

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        if frame_idx % sample_rate == 0:
            timestamp_sec = frame_idx / fps
            yield frame_idx, timestamp_sec, frame
            
        frame_idx += 1
        
    cap.release()
