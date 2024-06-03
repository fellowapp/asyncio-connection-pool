from functools import partial

from redis import asyncio as aioredis

from asyncio_connection_pool import ConnectionStrategy

__all__ = ("RedisConnectionStrategy",)


class RedisConnectionStrategy(ConnectionStrategy[aioredis.Redis]):
    def __init__(self, *args, **kwargs):
        self._create_redis = partial(aioredis.Redis, *args, **kwargs)

    async def make_connection(self) -> aioredis.Redis:
        return self._create_redis()

    def connection_is_closed(self, conn):
        return conn.closed

    async def close_connection(self, conn):
        conn.close()
        await conn.wait_closed()
