import os


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class MatchingConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.input_sqs_url = os.environ.get("MATCHING_INPUT_SQS_URL")
        self.output_sqs_url = os.environ.get("MATCHING_OUTPUT_SQS_URL")
        self.clipper_output_sqs_url = os.environ.get("CLIPPER_OUTPUT_SQS_URL")
        self.dlq_sqs_url = os.environ.get("MATCHING_DLQ_SQS_URL")

        self.sqlite_db_path = os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db")
        self.users_db_path = self.sqlite_db_path
        self.matches_db_path = self.sqlite_db_path

        self.match_threshold = float(os.environ.get("MATCH_THRESHOLD", "0.75"))
        self.min_similarity = float(
            os.environ.get("MIN_SIMILARITY", os.environ.get("MATCH_THRESHOLD", "0.75"))
        )
        self.min_margin = float(os.environ.get("MIN_MARGIN", os.environ.get("MARGIN", "0.05")))
        self.top_k_candidates = int(os.environ.get("TOP_K_CANDIDATES", "25"))
        self.min_track_embeddings = int(os.environ.get("MIN_TRACK_EMBEDDINGS", "3"))
        self.max_distance_std = float(os.environ.get("MAX_DISTANCE_STD", "0.08"))
        self.min_track_consistency = float(os.environ.get("MIN_TRACK_CONSISTENCY", "0.75"))
        self.margin = float(os.environ.get("MARGIN", "0.05"))
        self.min_score = float(os.environ.get("MIN_SCORE", str(self.min_similarity)))
        self.max_messages = int(os.environ.get("MATCHING_MAX_MESSAGES", "10"))
        self.backfill_batch_size = int(os.environ.get("MATCHING_BACKFILL_BATCH_SIZE", "100"))
        self.long_poll_seconds = int(os.environ.get("MATCHING_LONG_POLL_SECONDS", "20"))
        self.empty_queue_sleep_seconds = float(
            os.environ.get("MATCHING_EMPTY_QUEUE_SLEEP_SECONDS", "2")
        )
        self.error_backoff_seconds = float(
            os.environ.get("MATCHING_ERROR_BACKOFF_SECONDS", "5")
        )
        self.metrics_log_interval = int(
            os.environ.get("MATCHING_METRICS_LOG_INTERVAL", "25")
        )
        self.max_receive_count = int(
            os.environ.get("MATCHING_MAX_RECEIVE_COUNT", "5")
        )
        self.worker_lease_ttl_seconds = int(
            os.environ.get("MATCHING_LEASE_TTL_SECONDS", "60")
        )
        self.allow_single_embedding_debug = _as_bool(
            os.environ.get("ALLOW_SINGLE_EMBEDDING_DEBUG", "false")
        )

        if not self.input_sqs_url:
            raise ValueError("MATCHING_INPUT_SQS_URL is required")


config = MatchingConfig()
