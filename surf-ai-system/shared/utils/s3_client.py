import boto3
import os
import time
from botocore.exceptions import ClientError
from .logger import get_logger

logger = get_logger("s3_client")

class S3Client:
    def __init__(self, region_name: str = None):
        self.region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self.client = boto3.client('s3', region_name=self.region_name)

    def upload_file(self, file_path: str, bucket: str, object_name: str, max_retries: int = 3) -> bool:
        """
        Upload a file to an S3 bucket with exponential backoff retries.
        """
        attempt = 0
        backoff = 1.0  # initial backoff in seconds

        while attempt < max_retries:
            try:
                logger.info(f"Uploading {file_path} to s3://{bucket}/{object_name} (Attempt {attempt + 1}/{max_retries})")
                self.client.upload_file(file_path, bucket, object_name)
                logger.info(f"Upload successful: s3://{bucket}/{object_name}")
                return True
            except ClientError as e:
                logger.error(f"ClientError uploading {file_path} to S3: {e}")
            except Exception as e:
                logger.error(f"Unexpected error uploading to S3: {e}")
            
            attempt += 1
            if attempt < max_retries:
                logger.debug(f"Retrying S3 upload in {backoff} seconds...")
                time.sleep(backoff)
                backoff *= 2.0

        logger.error(f"Failed to upload {file_path} to s3://{bucket}/{object_name} after {max_retries} attempts.")
        return False
