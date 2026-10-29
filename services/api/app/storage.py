from io import BytesIO
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from .config import settings


class Storage:
    def __init__(self) -> None:
        self.cfg = settings()
        self.client = None
        if self.cfg.s3_endpoint:
            self.client = boto3.client(
                "s3",
                endpoint_url=self.cfg.s3_endpoint,
                aws_access_key_id=self.cfg.s3_access_key,
                aws_secret_access_key=self.cfg.s3_secret_key,
            )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        if self.client:
            try:
                self.client.head_bucket(Bucket=self.cfg.s3_bucket)
            except ClientError:
                self.client.create_bucket(Bucket=self.cfg.s3_bucket)
            self.client.put_object(
                Bucket=self.cfg.s3_bucket, Key=key, Body=BytesIO(data), ContentType=content_type
            )
        else:
            path = Path(self.cfg.local_storage) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def get(self, key: str) -> bytes:
        if self.client:
            return self.client.get_object(Bucket=self.cfg.s3_bucket, Key=key)["Body"].read()
        return (Path(self.cfg.local_storage) / key).read_bytes()

    def delete(self, key: str) -> None:
        if self.client:
            self.client.delete_object(Bucket=self.cfg.s3_bucket, Key=key)
        else:
            (Path(self.cfg.local_storage) / key).unlink(missing_ok=True)
