import os
import time
from typing import Any

import boto3


class MediaService:
    def __init__(self):
        self.default_bucket = os.environ.get("S3_BUCKET", "surf-ai-bucket")
        self.client = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        self._cache: dict[str, dict[str, Any]] = {}

    def get_presigned_url(
        self,
        s3_path: str | None = None,
        *,
        key: str | None = None,
        expires_in: int = 3600,
    ) -> str | None:
        if s3_path and s3_path.startswith(("http://", "https://")):
            return s3_path

        bucket, object_key = self._resolve_bucket_and_key(s3_path=s3_path, key=key)
        if not bucket or not object_key:
            return None

        cache_key = f"{bucket}/{object_key}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and cached["expires_at"] > now + 60:
            return cached["url"]

        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=expires_in,
        )
        self._cache[cache_key] = {
            "url": url,
            "expires_at": now + expires_in,
        }
        return url

    def _resolve_bucket_and_key(
        self,
        *,
        s3_path: str | None,
        key: str | None,
    ) -> tuple[str | None, str | None]:
        if key:
            return self.default_bucket, key

        if not s3_path:
            return None, None

        if s3_path.startswith("s3://"):
            without_scheme = s3_path[5:]
            bucket, _, object_key = without_scheme.partition("/")
            return bucket or None, object_key or None

        return self.default_bucket, s3_path.lstrip("/")
