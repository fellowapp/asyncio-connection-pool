from abc import ABC, abstractmethod
import asyncio
import inspect
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Generic, Optional, TypeVar

__all__ = "ConnectionPool", "ConnectionStrategy"
Conn = TypeVar("Conn")


class ConnectionStrategy(ABC, Generic[Conn]):
    @abstractmethod
    async def make_connection(self) -> Awaitable[Conn]:
        ...

    @abstractmethod
    def connection_is_closed(self, conn: Conn) -> bool:
        ...

    @abstractmethod
    async def close_connection(self, conn: Conn) -> None:
        ...


async def _close_connection_compat(
    strategy: ConnectionStrategy[Conn], conn: Conn
) -> None:
    result = strategy.close_connection(conn)
    if inspect.isawaitable(result):
        await result


class ConnectionPool(Generic[Conn]):
    """A high-throughput, optionally-burstable pool free of explicit locking.

    NOTE: Not threadsafe. Do not share across threads.

    This connection pool offers high throughput by avoiding the need for an
    explicit lock to retrieve a connection. This is possible by taking
    advantage of cooperative multitasking with asyncio.

    If the optional `burst_limit` argument is supplied, the `max_size` argument
    will act as a "soft" maximum. When there is demand, more connections will
    be opened to satisfy it, up to `burst_limit`. When these connections are no
    longer needed, they will be closed. This way we can avoid holding many open
    connections for extended times.

    This implementation assumes that all operations that do not await are
    atomic. Since CPython can switch thread contexts between each evaluated op
    code, it is not safe to share an instance of this pool between threads.

    This pool is generic over the type of connection it holds, which can be
    anything. Any logic specific to the connection type belongs in the
    ConnectionStrategy, which should be passed to the pool's constructor via
    the `strategy` parameter.
    """

    def __init__(
        self,
        *,
        strategy: ConnectionStrategy[Conn],
        max_size: int,
        burst_limit: Optional[int] = None
    ):
        self._loop = asyncio.get_event_loop()
        self.strategy = strategy
        self.max_size = max_size
        self.burst_limit = burst_limit
        if burst_limit is not None and burst_limit < max_size:
            raise ValueError("burst_limit must be greater than or equal to max_size")
        self.in_use = 0
        self.currently_allocating = 0
        self.currently_deallocating = 0
        self.available: "asyncio.Queue[Conn]" = asyncio.Queue(maxsize=self.max_size)

    @property
    def _total(self) -> int:
        return self.in_use + self.currently_allocating + self.available.qsize()

    @property
    def _waiters(self) -> int:
        waiters = self.available._getters  # type: ignore
        return sum(not (w.done() or w.cancelled()) for w in waiters)

    async def _connection_maker(self):
        try:
            conn = await self.strategy.make_connection()
        finally:
            self.currently_allocating -= 1
        self.in_use += 1
        return conn

    async def _connection_waiter(self):
        conn = await self.available.get()
        self.in_use += 1
        return conn

    def _get_conn(self) -> "Awaitable[Conn]":
        # This function is how we avoid explicitly locking. Since it is
        # synchronous, we do all the "book-keeping" required to get a
        # connection synchronously, and return a Future or Task which can be
        # awaited after this function returns.
        #
        # The most important thing here is that we do not await from when we
        # measure values like `self._total` or `self.available.empty()` until
        # we change values that affect those measurements. In other words,
        # taking a connection must be an atomic operation.
        if not self.available.empty():
            # Reserve a connection and wrap in a Future to make it awaitable.
            # Incidentally, awaiting a done Future doesn't involve yielding to
            # the event loop; it's more like getting the next value from a
            # generator.
            fut: "asyncio.Future[Conn]" = self._loop.create_future()
            fut.set_result(self.available.get_nowait())
            self.in_use += 1
            return fut
        elif self._total < self.max_size or (
            self.burst_limit is not None and self._total < self.burst_limit
        ):
            # Reserve a space for a connection and asynchronously make it.
            # Returns a Task that resolves to the new connection, which can be
            # awaited.
            #
            # If there are a lot of tasks waiting for a connection, to avoid
            # having all of them time out and be cancelled, we'll burst to
            # higher max_size.
            self.currently_allocating += 1
            return self._loop.create_task(self._connection_maker())
        else:
            # Return a Task that waits for the next connection to appear in the
            # queue.
            return self._loop.create_task(self._connection_waiter())

    @asynccontextmanager
    async def get_connection(self) -> AsyncIterator[Conn]:  # type: ignore
        # _get_conn atomically does any book-keeping and returns an awaitable
        # that resolves to a connection.
        conn = await self._get_conn()
        # Repeat until the connection we get is still open.
        while True:
            try:
                if not self.strategy.connection_is_closed(conn):
                    break
            except BaseException:
                self.in_use -= 1
                raise
            self.in_use -= 1  # Incremented in _get_conn
            conn = await self._get_conn()

        try:
            # Evaluate the body of the `async with` block.
            yield conn
        finally:
            # Return the connection to the pool.
            self.currently_deallocating += 1
            try:
                # Check if we are currently over-committed (i.e. bursting)
                if (
                    self._total - self.currently_deallocating >= self.max_size
                    and self._waiters == 0
                ):
                    # We had created extra connections to handle burst load,
                    # but there are no more waiters, so we don't need this
                    # connection anymore.
                    await _close_connection_compat(self.strategy, conn)
                else:
                    try:
                        self.available.put_nowait(conn)
                    except asyncio.QueueFull:
                        # We don't actually check if the queue has room before
                        # trying to put the connection into it. It's unclear
                        # whether we could have a full queue and still have
                        # waiters, but we should handle this case to be safe
                        # (otherwise we would leak connections).
                        await _close_connection_compat(self.strategy, conn)
            finally:
                # Consider the connection closed even if an exception is raised
                # in the strategy's close_connection.
                self.currently_deallocating -= 1
                self.in_use -= 1
                assert self.in_use >= 0, "More connections returned than given"
