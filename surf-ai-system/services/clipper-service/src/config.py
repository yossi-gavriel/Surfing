import os

class ClipperConfig:
    def __init__(self):
        self.aws_region = os.environ.get("AWS_REGION", "us-east-1")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        self.input_sqs_url = os.environ.get("CLIPPER_INPUT_SQS_URL")
        
        if not self.s3_bucket or not self.input_sqs_url:
            raise ValueError("S3_BUCKET and CLIPPER_INPUT_SQS_URL rigorously required structural implementations internally.")

config = ClipperConfig()
