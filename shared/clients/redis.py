"""Redis connection: used for caching and rate limits."""

from redis.asyncio import Redis

from shared.config import Settings


def create_redis(settings: Settings) -> Redis:
    """Create the client. It only connects when first used."""
    return Redis.from_url(str(settings.redis_url))


async def ping(client: Redis) -> None:
    """Send PING. Raises if Redis cannot be reached."""
    await client.ping()
