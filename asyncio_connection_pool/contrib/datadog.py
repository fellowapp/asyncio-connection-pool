from collections.abc import AsyncIterator, Awaitable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TypeVar

from datadog.dogstatsd.base import statsd
from ddtrace.trace import tracer

from asyncio_connection_pool import (
    ConnectionPool as _ConnectionPool,
)
from asyncio_connection_pool import (
    ConnectionStrategy,
)

__all__ = ("ConnectionPool",)
Conn = TypeVar("Conn")


class ConnectionPool(_ConnectionPool[Conn]):
    def __init__(
        self,
        service_name: str,
        *,
        extra_tags: list[str] | None = None,
        strategy: ConnectionStrategy[Conn],
        max_size: int,
        burst_limit: int | None = None,
    ):
        super().__init__(strategy=strategy, max_size=max_size, burst_limit=burst_limit)
        self._connections_acquiring = 0
        self._service_name = service_name
        self._is_bursting = False
        self._reported_hitting_burst_limit = False
        self._extra_tags = extra_tags or []
        self._loop.call_soon(self._periodically_send_metrics)

    def _periodically_send_metrics(self) -> None:
        try:
            self._record_pressure()
        finally:
            self._loop.call_later(60, self._periodically_send_metrics)

    def _record_pressure(self) -> None:
        statsd.gauge(
            f"{self._service_name}.pool.total_connections",
            self._total,
            tags=self._extra_tags,
        )
        statsd.gauge(
            f"{self._service_name}.pool.available_connections",
            self.available.qsize(),
            tags=self._extra_tags,
        )
        statsd.gauge(
            f"{self._service_name}.pool.waiting", self._waiters, tags=self._extra_tags
        )
        statsd.gauge(
            f"{self._service_name}.pool.connections_used",
            self.in_use,
            tags=self._extra_tags,
        )
        self._record_connection_acquiring()
        if self._total > self.max_size:
            if not self._is_bursting:
                self._is_bursting = True
                statsd.event(
                    f"{self._service_name} pool using burst capacity",
                    f"Pool max size of {self.max_size} will be exceeded temporarily, up to {self.burst_limit}",  # noqa E501
                    alert_type="warning",
                    tags=self._extra_tags,
                )
            if (
                self._total == self.burst_limit
                and not self._reported_hitting_burst_limit
            ):
                self._reported_hitting_burst_limit = True
                statsd.event(
                    f"{self._service_name} pool reached burst limit",
                    "There are not enough redis connections to satisfy all users",
                    alert_type="error",
                    tags=self._extra_tags,
                )
        elif self._is_bursting:
            self._is_bursting = False
            self._reported_hitting_burst_limit = False
            statsd.event(
                f"{self._service_name} pool no longer bursting",
                f"Number of connections has dropped below {self.max_size}",
                alert_type="success",
                tags=self._extra_tags,
            )

    def _record_connection_acquiring(self, value: int = 0) -> None:
        self._connections_acquiring += value

        statsd.gauge(
            f"{self._service_name}.pool.connections_acquiring",
            self._connections_acquiring,
            tags=self._extra_tags,
        )

    def _connection_maker(self):
        statsd.increment(
            f"{self._service_name}.pool.getting_connection",
            tags=[*self._extra_tags, "method:new"],
        )

        async def connection_maker(self) -> Conn:
            with tracer.trace(
                f"{self._service_name}.pool._create_new_connection",
                service=self._service_name,
            ):
                return await super()._connection_maker()

        return connection_maker(self)

    def _connection_waiter(self):
        statsd.increment(
            f"{self._service_name}.pool.getting_connection",
            tags=[*self._extra_tags, "method:wait"],
        )

        async def connection_waiter(self) -> Conn:
            with tracer.trace(
                f"{self._service_name}.pool._wait_for_connection",
                service=self._service_name,
            ):
                return await super()._connection_waiter()

        return connection_waiter(self)

    def _get_conn(self) -> Awaitable[Conn]:
        if not self.available.empty():
            statsd.increment(
                f"{self._service_name}.pool.getting_connection",
                tags=[*self._extra_tags, "method:available"],
            )
        return super()._get_conn()

    @asynccontextmanager
    async def get_connection(self) -> AsyncIterator[Conn]:
        async with AsyncExitStack() as stack:
            self._record_connection_acquiring(1)
            try:
                with tracer.trace(
                    f"{self._service_name}.pool.acquire_connection",
                    service=self._service_name,
                ):
                    conn = await stack.enter_async_context(super().get_connection())
            finally:
                self._record_connection_acquiring(-1)
            self._record_pressure()
            yield conn
        self._record_pressure()
