import os

class ClipperConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        self.input_sqs_url = os.environ.get("CLIPPER_INPUT_SQS_URL")
        self.dlq_sqs_url = os.environ.get("CLIPPER_DLQ_SQS_URL")
        self.max_receive_count = int(os.environ.get("CLIPPER_MAX_RECEIVE_COUNT", "5"))
        self.worker_lease_ttl_seconds = int(os.environ.get("CLIPPER_LEASE_TTL_SECONDS", "60"))
        self.metrics_log_interval = int(os.environ.get("CLIPPER_METRICS_LOG_INTERVAL", "25"))
        
        if not self.s3_bucket or not self.input_sqs_url:
            raise ValueError("S3_BUCKET and CLIPPER_INPUT_SQS_URL rigorously required structural implementations internally.")

config = ClipperConfig()
