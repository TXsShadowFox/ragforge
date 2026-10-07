"""Object storage for uploaded files.

We only use the standard S3 API (boto3), so any S3-compatible service works:
RustFS locally, AWS S3 or Cloudflare R2 in the cloud. Only the settings change.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

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
    """List buckets. Raises if storage cannot be reached or the keys are wrong.

    boto3 is synchronous, so we run it in a thread to keep the event loop free.
    """
    await asyncio.to_thread(client.list_buckets)
