import os

class EmbeddingConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        self.input_sqs_url = os.environ.get("EMBEDDING_INPUT_SQS_URL")
        self.output_sqs_url = os.environ.get("EMBEDDING_OUTPUT_SQS_URL")
        
        self.min_face_size = int(os.environ.get("MIN_FACE_SIZE", "40"))
        self.min_confidence = float(os.environ.get("MIN_CONFIDENCE", "0.5"))
        self.min_blur_score = float(os.environ.get("MIN_BLUR_SCORE", "50.0"))
        
        self.max_yaw = float(os.environ.get("MAX_YAW", "30.0"))
        self.max_pitch = float(os.environ.get("MAX_PITCH", "30.0"))
        self.max_similarity = float(os.environ.get("MAX_SIMILARITY", "0.95"))
        self.min_samples = int(os.environ.get("MIN_SAMPLES", "2"))
        
        self.model_name = os.environ.get("INSIGHTFACE_MODEL", "buffalo_s")
        self.ctx_id = int(os.environ.get("INSIGHTFACE_CTX", "-1"))
        
        if not self.s3_bucket or not self.input_sqs_url or not self.output_sqs_url:
            raise ValueError("S3_BUCKET, EMBEDDING_INPUT_SQS_URL, and EMBEDDING_OUTPUT_SQS_URL are required")

config = EmbeddingConfig()
