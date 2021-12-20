import asyncio
import pytest
from asyncio_connection_pool import ConnectionPool, ConnectionStrategy
from asyncio_connection_pool.contrib.datadog import (
    ConnectionPool as TracingConnectionPool,
)
from contextlib import asynccontextmanager
from functools import partial


@pytest.fixture(
    params=[ConnectionPool, partial(TracingConnectionPool, service_name="test")]
)
def pool_cls(request):
    return request.param


class RandomIntStrategy(ConnectionStrategy[int]):
    async def make_connection(self):
        import random

        return random.randint(0, 10000)

    def connection_is_closed(self, conn):
        return False

    def close_connection(self, conn):
        pass


def test_valid_burst_limit(pool_cls):
    """Test that invalid burst_limit values cause errors (only at construction time)"""
    strategy = RandomIntStrategy()
    pool_cls(strategy=strategy, max_size=100, burst_limit=None)
    pool_cls(strategy=strategy, max_size=100, burst_limit=100)
    pool_cls(strategy=strategy, max_size=100, burst_limit=101)
    with pytest.raises(ValueError):
        pool_cls(strategy=strategy, max_size=100, burst_limit=99)


class Counter:
    def __init__(self, goal):
        self.goal = goal
        self.n = 0
        self.ev = asyncio.Event()
        # Prevent waiting more than once on the same counter
        self._waiter = self.ev.wait()

    def wait(self):
        return self._waiter

    @asynccontextmanager
    async def inc(self):
        self.n += 1
        if self.n == self.goal:
            self.ev.set()
        try:
            yield self.n
        finally:
            self.n -= 1


@pytest.mark.asyncio
async def test_concurrent_get_connection(pool_cls):
    """Test handling several connection requests in a short time."""

    pool = pool_cls(strategy=RandomIntStrategy(), max_size=20)
    nworkers = 10
    counter = Counter(nworkers)
    stop = asyncio.Event()

    async def connection_holder():
        async with pool.get_connection():
            async with counter.inc():
                await stop.wait()

    coros = [asyncio.create_task(connection_holder()) for _ in range(nworkers)]
    await counter.wait()

    assert pool.in_use == nworkers, f"{nworkers} connections should be in use"
    assert pool.available.empty(), "There should not be any extra connections"

    stop.set()
    await asyncio.gather(*coros)

    assert pool.in_use == 0
    assert (
        pool.available.qsize() == nworkers
    ), f"{nworkers} connections should be allocated"


@pytest.mark.asyncio
async def test_currently_allocating(pool_cls):
    """Test that currently_allocating is accurate."""

    ev = asyncio.Event()

    class WaitStrategy(ConnectionStrategy[None]):
        async def make_connection(self):
            await ev.wait()

        def connection_is_closed(self, conn):
            return False

        def close_connection(self, conn):
            pass

    nworkers = 10
    pool = pool_cls(strategy=WaitStrategy(), max_size=50)
    counter = Counter(nworkers)
    counter2 = Counter(nworkers)
    ev2 = asyncio.Event()

    async def worker():
        async with counter.inc():
            async with pool.get_connection():
                async with counter2.inc():
                    await ev2.wait()

    coros = [asyncio.create_task(worker()) for _ in range(nworkers)]
    await counter.wait()
    await asyncio.sleep(0)

    assert (
        pool.currently_allocating == nworkers
    ), f"{nworkers} workers are waiting for a connection"
    ev.set()  # allow the workers to get their connections
    await counter2.wait()
    assert (
        pool.currently_allocating == 0 and pool.in_use == nworkers
    ), "all workers should have their connections now"
    ev2.set()
    await asyncio.gather(*coros)
    assert (
        pool.in_use == 0 and pool.available.qsize() == nworkers
    ), "all workers should have returned their connections"


@pytest.mark.asyncio
async def test_burst(pool_cls):
    """Test that bursting works when enabled and doesn't when not."""

    did_call_close_connection = asyncio.Event()

    class Strategy(RandomIntStrategy):
        def close_connection(self, conn):
            did_call_close_connection.set()
            return super().close_connection(conn)

    # Burst disabled initially
    pool = pool_cls(strategy=Strategy(), max_size=5)

    async def worker(counter, ev):
        async with pool.get_connection():
            async with counter.inc():
                await ev.wait()  # hold the connection until we say so

    # Use up the normal max_size of the pool
    main_event = asyncio.Event()
    counter = Counter(pool.max_size)
    coros = [
        asyncio.create_task(worker(counter, main_event)) for _ in range(pool.max_size)
    ]
    await counter.wait()

    with pytest.raises(asyncio.TimeoutError):
        # Burst is disabled, can't get a connection without waiting (this is a
        # deadlock)
        await asyncio.wait_for(worker(counter, main_event), timeout=0.25)

    # Add a burst size to the pool
    pool.burst_limit = pool.max_size + 1
    counter = Counter(goal=1)
    burst_event = asyncio.Event()
    burst_worker = asyncio.create_task(worker(counter, burst_event))
    await counter.wait()
    assert pool._total == pool.max_size + 1

    with pytest.raises(asyncio.TimeoutError):
        # We're at burst_limit, can't get a connection without waiting
        await asyncio.wait_for(worker(counter, burst_event), timeout=0.25)

    async def waiting_worker():
        async with pool.get_connection():
            pass

    coro = asyncio.create_task(waiting_worker())  # Join the queue
    await asyncio.sleep(0.1)  # Give it some time to start waiting
    assert pool._waiters == 1, "Worker should be waiting, we're at burst_limit already"
    burst_event.set()  # Allow worker holding burst connection to finish
    await burst_worker  # Wait for it to release the connection
    assert (
        not did_call_close_connection.is_set()
    ), "Did not churn the burst connection while there was a waiter"
    await coro  # Should be able to take that burst connection we created
    assert (
        did_call_close_connection.is_set()
    ), "No more waiters, burst connection should be closed"
    assert (
        pool._total == pool.max_size
    ), "Pool should return to max size after burst capacity is not needed"
    main_event.set()  # Allow the initial workers to exit
    await asyncio.gather(*coros)  # Wait for initial workers to exit
    assert (
        pool.available.qsize() == pool.max_size
    ), "Workers should return their connections to the pool"


@pytest.mark.asyncio
async def test_stale_connections(pool_cls):
    """Test that the pool doesn't hand out closed connections."""

    stale_connections = {1, 2, 3, 4}

    class Strategy(ConnectionStrategy[int]):
        def __init__(self):
            from itertools import count

            self.it = iter(count())

        async def make_connection(self):
            return next(self.it)

        def connection_is_closed(self, conn):
            return conn in stale_connections

        def close_connection(self, conn):
            stale_connections.add(conn)

    pool = pool_cls(strategy=Strategy(), max_size=10)

    async def worker():
        async with pool.get_connection() as c:
            return c

    conns = await asyncio.gather(*[worker() for _ in range(10)])
    assert not stale_connections & set(conns)

    pool = pool_cls(strategy=Strategy(), max_size=1)

    async with pool.get_connection() as conn:
        now_stale = conn
        stale_connections.add(conn)

    async with pool.get_connection() as conn:
        assert (
            conn != now_stale
        ), "Make sure connections closed by consumers are not given back out"


@pytest.mark.asyncio
async def test_handling_cancellederror():
    making_connection = asyncio.Event()

    class Strategy(ConnectionStrategy[int]):
        async def make_connection(self):
            making_connection.set()
            await asyncio.Event().wait()  # wait forever
            return 1

        def connection_is_closed(self, conn):
            return False

        def close_connection(self, conn):
            pass

    pool: TracingConnectionPool[int] = TracingConnectionPool(
        strategy=Strategy(), max_size=3, service_name="test"
    )
    cancelled = asyncio.Event()

    async def worker():
        try:
            async with pool.get_connection():
                pass
        finally:
            cancelled.set()

    t = asyncio.create_task(worker())
    await making_connection.wait()

    assert pool._connections_acquiring == 1
    t.cancel()
    await cancelled.wait()
    assert pool._connections_acquiring == 0
