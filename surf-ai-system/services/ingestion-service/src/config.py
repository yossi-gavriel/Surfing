import os
import json
from typing import List, Dict, Any

class IngestionConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        self.sqs_queue_url = os.environ.get("SQS_QUEUE_URL")
        self.chunk_duration = int(os.environ.get("CHUNK_DURATION", "10"))
        self.sqlite_db_path = os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db")
        self.camera_poll_interval = int(os.environ.get("CAMERA_POLL_INTERVAL", "10"))
        self.worker_lease_ttl_seconds = int(os.environ.get("INGESTION_LEASE_TTL_SECONDS", "60"))
        self.metrics_log_interval = int(os.environ.get("INGESTION_METRICS_LOG_INTERVAL", "25"))
        
        if not self.s3_bucket:
            raise ValueError("S3_BUCKET environment variable is required")
        if not self.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL environment variable is required")
            
        self.cameras = self._load_cameras()

    def _load_cameras(self) -> List[Dict[str, Any]]:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        default_config_path = os.path.abspath(os.path.join(current_dir, "../../../config/cameras.json"))
        
        docker_config_path = "/app/config/cameras.json"
        
        if os.environ.get("CAMERAS_CONFIG_PATH"):
            config_path = os.environ.get("CAMERAS_CONFIG_PATH")
        elif os.path.exists(docker_config_path):
            config_path = docker_config_path
        else:
            config_path = default_config_path

        try:
            with open(config_path, "r") as f:
                data = json.load(f)
                return data.get("cameras", [])
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            raise ValueError(f"Cameras config file at {config_path} is invalid JSON")

config = IngestionConfig()
