"""Object storage for uploaded files.

We only use the standard S3 API (boto3), so any S3-compatible service works:
RustFS locally, AWS S3 or Cloudflare R2 in the cloud. Only the settings change.
boto3 is synchronous, so every call runs in a thread to keep the event loop free.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from shared.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def create_s3_client(settings: Settings) -> S3Client:
    """Create the client. It only connects when first used."""
    config = Config(
        # Put the bucket name in the URL path (http://host/bucket/key), not in the
        # host name. Local S3 servers like RustFS need this.
        s3={"addressing_style": "path"},
        # New boto3 versions add extra checksums by default. Some S3-compatible
        # servers reject them, so we only send checksums when S3 requires them.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        connect_timeout=2,
        read_timeout=10,
        retries={"max_attempts": 3, "mode": "standard"},
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
        config=config,
    )


async def ping(client: S3Client) -> None:
    """List buckets. Raises if storage cannot be reached or the keys are wrong."""
    await asyncio.to_thread(client.list_buckets)


async def ensure_bucket(client: S3Client, bucket: str) -> None:
    """Create the bucket if it does not exist yet. Safe to call many times."""

    def ensure() -> None:
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                raise
            # Another process may create it at the same moment: that is fine.
            with contextlib.suppress(client.exceptions.BucketAlreadyOwnedByYou):
                client.create_bucket(Bucket=bucket)

    await asyncio.to_thread(ensure)


async def upload_file(
    client: S3Client, bucket: str, key: str, file: BinaryIO, content_type: str
) -> None:
    """Upload a file object (big files go up in parts)."""
    await asyncio.to_thread(
        client.upload_fileobj, file, bucket, key, ExtraArgs={"ContentType": content_type}
    )


async def download_file(client: S3Client, bucket: str, key: str) -> bytes:
    def download() -> bytes:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()

    return await asyncio.to_thread(download)


async def delete_file(client: S3Client, bucket: str, key: str) -> None:
    """Delete a file. Deleting a file that is not there is not an error (S3 works this way)."""
    await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=key)


async def file_exists(client: S3Client, bucket: str, key: str) -> bool:
    def exists() -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    return await asyncio.to_thread(exists)
