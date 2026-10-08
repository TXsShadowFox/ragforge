"""Redis connection: used for the answer cache and the rate limits.

Both are helpers: when Redis has a problem, the API keeps working without them. So the
client fails fast (short timeouts, no retries) instead of making every request wait.
"""

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from shared.config import Settings

TIMEOUT_SECONDS = 0.5


def create_redis(settings: Settings) -> Redis:
    """Create the client. It only connects when first used."""
    return Redis.from_url(
        str(settings.redis_url),
        socket_timeout=TIMEOUT_SECONDS,
        socket_connect_timeout=TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), retries=0),  # redis-py retries 3 times by default
    )


async def ping(client: Redis) -> None:
    """Send PING. Raises if Redis cannot be reached."""
    await client.ping()
