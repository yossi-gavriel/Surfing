import os

class FrameProcessorConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        self.input_sqs_url = os.environ.get("INPUT_SQS_URL")
        self.output_sqs_url = os.environ.get("OUTPUT_SQS_URL")
        
        self.redis_host = os.environ.get("REDIS_HOST", "redis")
        self.redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        
        self.frame_sample_rate = int(os.environ.get("FRAME_SAMPLE_RATE", "5"))
        self.min_track_length = int(os.environ.get("MIN_TRACK_LENGTH", "3"))
        self.max_active_tracks = int(os.environ.get("MAX_ACTIVE_TRACKS", "10"))
        
        self.model_name = os.environ.get("YOLO_MODEL", "yolov8n.pt")
        self.min_confidence = float(os.environ.get("MIN_CONFIDENCE", "0.5"))
        self.inference_width = int(os.environ.get("INFERENCE_WIDTH", "640"))
        self.inference_height = int(os.environ.get("INFERENCE_HEIGHT", "640"))
        
        # Advanced tracking parameters
        self.min_bbox_area = int(os.environ.get("MIN_BBOX_AREA", "500"))
        self.max_aspect_ratio = float(os.environ.get("MAX_ASPECT_RATIO", "3.0"))
        self.center_dist_threshold = float(os.environ.get("CENTER_DIST_THRESHOLD", "100.0"))
        self.min_track_score = float(os.environ.get("MIN_TRACK_SCORE", "0.3"))
        
        # Tuning Variables
        self.max_velocity = float(os.environ.get("MAX_VELOCITY", "75.0"))
        self.conf_decay = float(os.environ.get("CONF_DECAY", "0.9"))
        
        # Debugging
        self.debug_mode = os.environ.get("DEBUG_MODE", "false").lower() == "true"
        self.debug_output_dir = os.environ.get("DEBUG_OUTPUT_DIR", "/tmp/debug_frames")
        
        if not self.s3_bucket or not self.input_sqs_url or not self.output_sqs_url:
            raise ValueError("S3_BUCKET, INPUT_SQS_URL, and OUTPUT_SQS_URL are required")

config = FrameProcessorConfig()
