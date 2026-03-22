import subprocess
import os
import time
from shared.utils.logger import get_logger

logger = get_logger("ffmpeg_runner")

class FFmpegRunner:
    def __init__(self, camera_id: str, rtsp_url: str, chunk_duration: int, output_dir: str):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.chunk_duration = chunk_duration
        self.output_dir = output_dir
        self.process = None
        self._running = False
        
        os.makedirs(self.output_dir, exist_ok=True)

    def start(self):
        """Starts the FFmpeg process to record chunks."""
        self._running = True
        
        while self._running:
            logger.info(f"[{self.camera_id}] Starting FFmpeg process for RTSP stream")
            
            # strftime format matches: cam1_YYYYMMDD_HHMMSS.ts
            output_pattern = os.path.join(self.output_dir, f"{self.camera_id}_%Y%m%d_%H%M%S.ts")
            
            cmd = [
                "ffmpeg",
                "-y",
                "-rtsp_transport", "tcp",
                "-i", self.rtsp_url,
                "-c", "copy",
                "-f", "segment",
                "-segment_time", str(self.chunk_duration),
                "-reset_timestamps", "1",
                "-strftime", "1",
                output_pattern
            ]
            
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                # Block until process exits completely or is terminated
                self.process.communicate()
                
                if self._running:
                    logger.warning(f"[{self.camera_id}] FFmpeg process exited unexpectedly. Restarting in 5s...")
                    time.sleep(5)
            except Exception as e:
                logger.error(f"[{self.camera_id}] Error running FFmpeg: {e}")
                if self._running:
                    time.sleep(5)

    def stop(self):
        """Stops the FFmpeg process gracefully."""
        self._running = False
        if self.process:
            logger.info(f"[{self.camera_id}] Terminating FFmpeg process")
            self.process.terminate()
            try:
                # Provide time for graceful segment flush
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{self.camera_id}] FFmpeg shutdown timeout elapsed, forcing kill")
                self.process.kill()
                self.process.wait()
            self.process = None
            logger.info(f"[{self.camera_id}] FFmpeg stopped")
