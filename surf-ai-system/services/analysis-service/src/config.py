import os


FAILURE_CODES = {
    "clip_corrupt": {"retryable": False, "max_retries": 0},
    "clip_too_short": {"retryable": False, "max_retries": 0},
    "no_surfer_detected": {"retryable": False, "max_retries": 0},
    "clip_download_failed": {"retryable": True, "max_retries": 3},
    "model_load_failed": {"retryable": True, "max_retries": 2},
    "s3_write_failed": {"retryable": True, "max_retries": 3},
    "db_write_failed": {"retryable": True, "max_retries": 3},
    "timeout": {"retryable": True, "max_retries": 2},
    "internal_error": {"retryable": True, "max_retries": 3},
}

PROCESSING_TIMEOUT_SECONDS = 280
SQS_VISIBILITY_TIMEOUT = 300

MODEL_VERSION = "wave_surfer_v1.0"


class AnalysisConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        self.input_sqs_url = os.environ.get("ANALYSIS_INPUT_SQS_URL")
        self.dlq_sqs_url = os.environ.get("ANALYSIS_DLQ_SQS_URL")
        self.max_receive_count = int(os.environ.get("ANALYSIS_MAX_RECEIVE_COUNT", "5"))
        self.worker_lease_ttl_seconds = int(os.environ.get("ANALYSIS_LEASE_TTL_SECONDS", "60"))
        self.metrics_log_interval = int(os.environ.get("ANALYSIS_METRICS_LOG_INTERVAL", "10"))
        self.debug_artifacts_enabled = os.environ.get("ANALYSIS_DEBUG_ARTIFACTS", "true").lower() in ("1", "true", "yes")

        # Model paths and versioning
        self.model_version = os.environ.get("ANALYSIS_MODEL_VERSION", "wave_surfer_v1.0")
        self.yolo_model_path = os.environ.get("ANALYSIS_YOLO_MODEL_PATH", "/app/services/analysis-service/models/yolo_wave_surfer.pt")

        # Detection thresholds (defaults; overridden by system_config at runtime)
        self.default_sample_fps = int(os.environ.get("ANALYSIS_SAMPLE_FPS", "10"))
        self.default_surfer_confidence = float(os.environ.get("ANALYSIS_SURFER_CONFIDENCE", "0.5"))
        self.default_wave_confidence = float(os.environ.get("ANALYSIS_WAVE_CONFIDENCE", "0.3"))

        if not self.s3_bucket:
            raise ValueError("S3_BUCKET is required for analysis service")
        if not self.input_sqs_url:
            raise ValueError("ANALYSIS_INPUT_SQS_URL is required for analysis service")


config = AnalysisConfig()
