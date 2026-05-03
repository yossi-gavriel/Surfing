"""Tests for failure paths in the analysis service.

Validates that each failure scenario produces the correct:
- final job status
- failure_code
- retry_count behavior
- isolation from main clip flow
"""

import sys
import os
import json
import time
import sqlite3
import tempfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

# Set required env vars before importing config (module-level AnalysisConfig instantiation)
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("ANALYSIS_INPUT_SQS_URL", "https://sqs.us-east-1.amazonaws.com/000/test-queue")

# Mock cv2 and ultralytics if not installed (they're not needed for these tests)
if "cv2" not in sys.modules:
    sys.modules["cv2"] = MagicMock()
if "ultralytics" not in sys.modules:
    sys.modules["ultralytics"] = MagicMock()

from src.config import FAILURE_CODES


# ── 1. Failure code classification tests ──

def test_failure_codes_classify_correctly():
    """Each failure code has explicit retryable + max_retries."""
    for code, spec in FAILURE_CODES.items():
        assert "retryable" in spec, f"{code} missing retryable field"
        assert "max_retries" in spec, f"{code} missing max_retries field"
        assert isinstance(spec["retryable"], bool), f"{code} retryable must be bool"
        assert isinstance(spec["max_retries"], int), f"{code} max_retries must be int"


def test_non_retryable_codes_have_zero_retries():
    """Non-retryable failures must have max_retries=0."""
    for code, spec in FAILURE_CODES.items():
        if not spec["retryable"]:
            assert spec["max_retries"] == 0, f"{code} is non-retryable but max_retries={spec['max_retries']}"


def test_clip_corrupt_is_non_retryable():
    assert FAILURE_CODES["clip_corrupt"]["retryable"] is False


def test_clip_too_short_is_non_retryable():
    assert FAILURE_CODES["clip_too_short"]["retryable"] is False


def test_no_surfer_detected_is_non_retryable():
    assert FAILURE_CODES["no_surfer_detected"]["retryable"] is False


def test_clip_download_failed_is_retryable():
    assert FAILURE_CODES["clip_download_failed"]["retryable"] is True
    assert FAILURE_CODES["clip_download_failed"]["max_retries"] >= 1


def test_model_load_failed_is_retryable():
    assert FAILURE_CODES["model_load_failed"]["retryable"] is True
    assert FAILURE_CODES["model_load_failed"]["max_retries"] >= 1


def test_s3_write_failed_is_retryable():
    assert FAILURE_CODES["s3_write_failed"]["retryable"] is True
    assert FAILURE_CODES["s3_write_failed"]["max_retries"] >= 1


def test_timeout_is_retryable():
    assert FAILURE_CODES["timeout"]["retryable"] is True
    assert FAILURE_CODES["timeout"]["max_retries"] >= 1


def test_internal_error_is_retryable():
    assert FAILURE_CODES["internal_error"]["retryable"] is True


# ── 2. Analyzer failure path tests ──

def test_analyzer_returns_clip_too_short():
    """Clip with < 3 frames returns clip_too_short failure."""
    from unittest.mock import MagicMock, patch
    from src.analyzer import RideAnalyzer
    from src.config import AnalysisConfig

    mock_config = MagicMock(spec=AnalysisConfig)
    mock_config.s3_bucket = "test-bucket"
    mock_config.default_sample_fps = 10
    mock_config.default_surfer_confidence = 0.5
    mock_config.default_wave_confidence = 0.3
    mock_config.debug_artifacts_enabled = False
    mock_config.yolo_model_path = "/nonexistent/model.pt"

    mock_s3 = MagicMock()
    mock_logger = MagicMock()

    analyzer = RideAnalyzer.__new__(RideAnalyzer)
    analyzer.config = mock_config
    analyzer.s3_client = mock_s3
    analyzer.logger = mock_logger
    analyzer._detector = None
    analyzer._maneuver_detector = None

    # Simulate _analyze_local with only 2 frames
    import numpy as np
    from src.frame_loader import FrameInfo

    with patch.object(analyzer, '_download_clip', return_value='/tmp/fake.mp4'):
        with patch('src.analyzer.extract_frames') as mock_extract:
            mock_extract.return_value = iter([
                FrameInfo(0, 0.0, np.zeros((100, 100, 3), dtype=np.uint8)),
                FrameInfo(1, 100.0, np.zeros((100, 100, 3), dtype=np.uint8)),
            ])
            with patch('src.analyzer.get_clip_metadata'):
                result = analyzer.analyze({"track_id": "test-short", "clip_s3": "test.mp4"})

    assert result["failure_code"] == "clip_too_short"
    assert "2 frames" in result["failure_reason"]


def test_analyzer_returns_no_surfer_detected():
    """All frames with no surfer detection returns no_surfer_detected."""
    from unittest.mock import MagicMock, patch
    from src.analyzer import RideAnalyzer
    from src.config import AnalysisConfig

    mock_config = MagicMock(spec=AnalysisConfig)
    mock_config.s3_bucket = "test-bucket"
    mock_config.default_sample_fps = 10
    mock_config.default_surfer_confidence = 0.5
    mock_config.default_wave_confidence = 0.3
    mock_config.debug_artifacts_enabled = False

    mock_s3 = MagicMock()
    mock_logger = MagicMock()

    analyzer = RideAnalyzer.__new__(RideAnalyzer)
    analyzer.config = mock_config
    analyzer.s3_client = mock_s3
    analyzer.logger = mock_logger
    analyzer._detector = MagicMock()
    analyzer._detector.detect.return_value = []  # No detections
    analyzer._maneuver_detector = None

    import numpy as np
    from src.frame_loader import FrameInfo, ClipMetadata

    frames = [
        FrameInfo(i, i * 100.0, np.zeros((100, 100, 3), dtype=np.uint8))
        for i in range(10)
    ]

    with patch.object(analyzer, '_download_clip', return_value='/tmp/fake.mp4'):
        with patch('src.analyzer.extract_frames', return_value=iter(frames)):
            with patch('src.analyzer.get_clip_metadata', return_value=ClipMetadata(
                duration_seconds=1.0, total_frames=10, sampled_frames=10,
                native_fps=30.0, resolution=(1920, 1080), file_size_bytes=1000,
            )):
                result = analyzer.analyze({"track_id": "test-nosurfer", "clip_s3": "test.mp4"})

    assert result["failure_code"] == "no_surfer_detected"


def test_analyzer_returns_clip_download_failed():
    """S3 download failure returns clip_download_failed."""
    from unittest.mock import MagicMock, patch
    from src.analyzer import RideAnalyzer
    from src.config import AnalysisConfig

    mock_config = MagicMock(spec=AnalysisConfig)
    mock_config.s3_bucket = "test-bucket"

    mock_s3 = MagicMock()
    mock_logger = MagicMock()

    analyzer = RideAnalyzer.__new__(RideAnalyzer)
    analyzer.config = mock_config
    analyzer.s3_client = mock_s3
    analyzer.logger = mock_logger
    analyzer._detector = None
    analyzer._maneuver_detector = None

    with patch.object(analyzer, '_download_clip', side_effect=Exception("Connection refused")):
        result = analyzer.analyze({"track_id": "test-dl-fail", "clip_s3": "test.mp4"})

    assert result["failure_code"] == "clip_download_failed"
    assert "Connection refused" in result["failure_reason"]


def test_analyzer_returns_s3_write_failed():
    """S3 canonical write failure returns s3_write_failed."""
    from unittest.mock import MagicMock, patch
    from src.analyzer import RideAnalyzer
    from src.config import AnalysisConfig
    from src.detector import Detection

    mock_config = MagicMock(spec=AnalysisConfig)
    mock_config.s3_bucket = "test-bucket"
    mock_config.default_sample_fps = 10
    mock_config.default_surfer_confidence = 0.5
    mock_config.default_wave_confidence = 0.3
    mock_config.debug_artifacts_enabled = False

    mock_s3 = MagicMock()
    mock_s3.put_object.side_effect = Exception("Access Denied")
    mock_logger = MagicMock()

    analyzer = RideAnalyzer.__new__(RideAnalyzer)
    analyzer.config = mock_config
    analyzer.s3_client = mock_s3
    analyzer.logger = mock_logger
    analyzer._maneuver_detector = None

    # Mock detector to return a surfer
    mock_detector = MagicMock()
    mock_detector.detect.return_value = [
        Detection(label="surfer", confidence=0.9, bbox=[100, 200, 200, 400]),
    ]
    analyzer._detector = mock_detector

    import numpy as np
    from src.frame_loader import FrameInfo, ClipMetadata

    frames = [
        FrameInfo(i, i * 100.0, np.zeros((100, 100, 3), dtype=np.uint8))
        for i in range(15)
    ]

    with patch.object(analyzer, '_download_clip', return_value='/tmp/fake.mp4'):
        with patch('src.analyzer.extract_frames', return_value=iter(frames)):
            with patch('src.analyzer.get_clip_metadata', return_value=ClipMetadata(
                duration_seconds=1.5, total_frames=15, sampled_frames=15,
                native_fps=30.0, resolution=(1920, 1080), file_size_bytes=5000,
            )):
                result = analyzer.analyze({
                    "track_id": "test-s3-fail", "clip_s3": "test.mp4",
                    "video_id": "v1", "user_id": "u1", "pool_id": "p1",
                })

    assert result["failure_code"] == "s3_write_failed"


# ── 3. Model load failure test ──

def test_detector_init_calls_yolo_with_path():
    """WaveSurferDetector passes model_path to YOLO constructor.

    If the file doesn't exist, YOLO will raise. We verify the init code
    calls YOLO(model_path) so that missing weights propagate as errors.
    """
    import ast
    detector_path = os.path.join(os.path.dirname(__file__), "..", "src", "detector.py")
    with open(detector_path) as f:
        source = f.read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WaveSurferDetector":
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name == "__init__":
                    # Verify YOLO(model_path) call exists
                    for child in ast.walk(method):
                        if isinstance(child, ast.Call):
                            func_name = ""
                            if isinstance(child.func, ast.Name):
                                func_name = child.func.id
                            if func_name == "YOLO" and len(child.args) >= 1:
                                return  # Found YOLO(model_path) call
    assert False, "WaveSurferDetector.__init__ must call YOLO(model_path)"


def test_analyzer_lazy_loads_detector():
    """RideAnalyzer._get_detector creates detector lazily, so model load
    failure happens at first use, not at service startup."""
    import ast
    analyzer_path = os.path.join(os.path.dirname(__file__), "..", "src", "analyzer.py")
    with open(analyzer_path) as f:
        source = f.read()

    # Verify _get_detector pattern: checks if self._detector is None
    assert "_get_detector" in source
    assert "self._detector is None" in source


# ── 4. Job lifecycle tests (DB) ──

def test_job_lifecycle_in_db():
    """Validate job creation, retry increment, and status transitions."""
    from shared.utils.pipeline_store import PipelineStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = PipelineStore(db_path)
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        # Create job
        with store.store.connection() as conn:
            conn.execute(
                """INSERT INTO analysis_jobs
                   (job_id, track_id, video_id, user_id, pool_id, camera_id,
                    status, retry_count, retryable, clip_s3, model_version,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, 1, ?, ?, ?, ?)""",
                ("j1", "t1", "v1", "u1", "p1", "c1", "s3://bucket/clip.mp4",
                 "wave_surfer_v1.0", now, now),
            )

        # Verify initial state
        job = store.get_analysis_job("t1")
        assert job is not None
        assert job["status"] == "pending"
        assert job["retry_count"] == 0

        # Simulate processing → failed
        with store.store.connection() as conn:
            conn.execute(
                """UPDATE analysis_jobs SET status = 'failed', failure_code = 'timeout',
                   failure_reason = 'timed out', retry_count = 1, updated_at = ?
                   WHERE track_id = ?""",
                (now, "t1"),
            )

        job = store.get_analysis_job("t1")
        assert job["status"] == "failed"
        assert job["failure_code"] == "timeout"
        assert job["retry_count"] == 1

        # Simulate retry → completed
        with store.store.connection() as conn:
            conn.execute(
                """UPDATE analysis_jobs SET status = 'completed', failure_code = NULL,
                   ride_score = 7.5, maneuver_count = 3, retry_count = 2,
                   completed_at = ?, updated_at = ?
                   WHERE track_id = ?""",
                (now, now, "t1"),
            )

        job = store.get_analysis_job("t1")
        assert job["status"] == "completed"
        assert job["failure_code"] is None
        assert job["ride_score"] == 7.5
        assert job["retry_count"] == 2
    finally:
        os.unlink(db_path)


def test_user_scoped_queries():
    """list_analysis_jobs_for_user only returns that user's jobs."""
    from shared.utils.pipeline_store import PipelineStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = PipelineStore(db_path)
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        with store.store.connection() as conn:
            for uid, tid in [("user-a", "track-a1"), ("user-a", "track-a2"), ("user-b", "track-b1")]:
                conn.execute(
                    """INSERT INTO analysis_jobs
                       (job_id, track_id, video_id, user_id, pool_id, camera_id,
                        status, retry_count, retryable, clip_s3, model_version,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'completed', 0, 1, 'clip.mp4', 'v1', ?, ?)""",
                    (f"j-{tid}", tid, "v1", uid, "p1", "c1", now, now),
                )

        user_a_jobs = store.list_analysis_jobs_for_user("user-a")
        user_b_jobs = store.list_analysis_jobs_for_user("user-b")

        assert len(user_a_jobs) == 2
        assert len(user_b_jobs) == 1
        assert all(j["user_id"] == "user-a" for j in user_a_jobs)
        assert all(j["user_id"] == "user-b" for j in user_b_jobs)
    finally:
        os.unlink(db_path)


def test_pool_scoped_queries():
    """list_analysis_jobs_for_pool returns pool-scoped results."""
    from shared.utils.pipeline_store import PipelineStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = PipelineStore(db_path)
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        with store.store.connection() as conn:
            for pool, tid in [("pool-x", "t-x1"), ("pool-x", "t-x2"), ("pool-y", "t-y1")]:
                conn.execute(
                    """INSERT INTO analysis_jobs
                       (job_id, track_id, video_id, user_id, pool_id, camera_id,
                        status, retry_count, retryable, clip_s3, model_version,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'completed', 0, 1, 'clip.mp4', 'v1', ?, ?)""",
                    (f"j-{tid}", tid, "v1", "u1", pool, "c1", now, now),
                )

        pool_x = store.list_analysis_jobs_for_pool("pool-x")
        pool_y = store.list_analysis_jobs_for_pool("pool-y")

        assert len(pool_x) == 2
        assert len(pool_y) == 1
    finally:
        os.unlink(db_path)


# ── 5. Main clip flow isolation test ──

def test_analysis_publish_failure_does_not_block_clipper():
    """Analysis queue publish failure in clipper is non-blocking (try/except with warning)."""
    # This is a code-path validation test — the clipper wraps analysis publishing
    # in try/except (lines 164-171 of clipper main.py). We verify the code structure.
    import ast
    clipper_main_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "clipper-service", "src", "main.py"
    )
    with open(clipper_main_path) as f:
        source = f.read()

    tree = ast.parse(source)

    # Find the process_clip function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_clip":
            # Find try/except blocks that contain "analysis" in string literals
            for child in ast.walk(node):
                if isinstance(child, ast.Try):
                    # Check if the handler catches the analysis publish and logs warning
                    for handler in child.handlers:
                        for hchild in ast.walk(handler):
                            if isinstance(hchild, ast.Constant) and isinstance(hchild.value, str):
                                if "analysis" in hchild.value.lower() and "non-blocking" in hchild.value.lower():
                                    return  # Found the non-blocking handler
    assert False, "Clipper process_clip must wrap analysis publish in try/except with non-blocking log"


# ── 6. Timeout configuration test ──

def test_processing_timeout_less_than_sqs_visibility():
    """PROCESSING_TIMEOUT_SECONDS must be < SQS_VISIBILITY_TIMEOUT to prevent double-processing."""
    from src.config import PROCESSING_TIMEOUT_SECONDS, SQS_VISIBILITY_TIMEOUT
    assert PROCESSING_TIMEOUT_SECONDS < SQS_VISIBILITY_TIMEOUT, (
        f"PROCESSING_TIMEOUT_SECONDS ({PROCESSING_TIMEOUT_SECONDS}) must be < "
        f"SQS_VISIBILITY_TIMEOUT ({SQS_VISIBILITY_TIMEOUT})"
    )


if __name__ == "__main__":
    test_failure_codes_classify_correctly()
    test_non_retryable_codes_have_zero_retries()
    test_clip_corrupt_is_non_retryable()
    test_clip_too_short_is_non_retryable()
    test_no_surfer_detected_is_non_retryable()
    test_clip_download_failed_is_retryable()
    test_model_load_failed_is_retryable()
    test_s3_write_failed_is_retryable()
    test_timeout_is_retryable()
    test_internal_error_is_retryable()
    test_analyzer_returns_clip_too_short()
    test_analyzer_returns_no_surfer_detected()
    test_analyzer_returns_clip_download_failed()
    test_analyzer_returns_s3_write_failed()
    test_detector_init_calls_yolo_with_path()
    test_analyzer_lazy_loads_detector()
    test_job_lifecycle_in_db()
    test_user_scoped_queries()
    test_pool_scoped_queries()
    test_analysis_publish_failure_does_not_block_clipper()
    test_processing_timeout_less_than_sqs_visibility()
    print("All failure path tests passed.")
