# asyncio-connection-pool

[![GitHub Workflow Status (main)](https://img.shields.io/github/workflow/status/fellowinsights/asyncio-connection-pool/CI/main?style=plastic)][main CI]
[![PyPI](https://img.shields.io/pypi/v/asyncio-connection-pool?style=plastic)][package]
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/asyncio-connection-pool?style=plastic)][package]

[main CI]: https://github.com/fellowinsights/asyncio-connection-pool/actions?query=workflow%3ACI+branch%3Amain
[package]: https://pypi.org/project/asyncio-connection-pool/

This is a generic, high-throughput, optionally-burstable pool for asyncio.

Some cool features:

- No locking[^1]; no `asyncio.Lock` or `asyncio.Condition` needs to be taken in
  order to get a connection.
- Available connections are retrieved without yielding to the event loop.
- When `burst_limit` is specified, `max_size` acts as a "soft" limit; the pool
  can go beyond this limit to handle increased load, and shrinks back down
  after.
- The contents of the pool can be anything; just implement a
  `ConnectionStrategy`.

[^1]: Theoretically, there is an implicit "lock" that is held while an asyncio
      task is executing. No other task can execute until the current task
      yields (since it's cooperative multitasking), so any operations during
      that time are atomic.

## Why?

We were using a different pool for handling our Redis connections, and noticed
that, under heavy load, we would spend a lot of time waiting for the lock, even
when there were available connections in the pool.

We also thought it would be nice if we didn't need to keep many connections
open when they weren't needed, but still have the ability to make more when
they are required.


## API


### `asyncio_connection_pool.ConnectionPool`

This is the implementation of the pool. It is generic over a type of
connection, and all implementation-specific logic is contained within a
[`ConnectionStrategy`](#asyncio_connection_poolconnectionstrategy).

A pool is created as follows:

```python
from asyncio_connection_pool import ConnectionPool

pool = ConnectionPool(strategy=my_strategy, max_size=15)
```

The constructor can optionally be passed an integer as `burst_limit`. This
allows the pool to open more connections than `max_size` temporarily.


#### `@asynccontextmanager async def get_connection(self) -> AsyncIterator[Conn]`

This method is the only way to get a connection from the pool. It is expected
to be used as follows:

```python
pool = ConnectionPool(...)

async with pool.get_connection() as conn:
    # Use the connection
    pass
```

When the `async with` block is entered, a connection is retrieved. If a
connection needs to be opened or if the pool is at capacity and no connections
are available, the caller will yield to the event loop.

When the block is exited, the connection will be returned to the pool.


### `asyncio_connection_pool.ConnectionStrategy`

This is an abstract class that defines the interface of the object passed as
`strategy`. A subclass _must_ implement the following methods:


#### `async def create_connection(self) -> Awaitable[Conn]`

This method is called to create a new connection to the resource. This happens
when a connection is requested and all connections are in use, as long as the
pool is not at capacity.

The result of a call to this method is what will be provided to a consumer of
the pool, and in most cases will be stored in the pool to be re-used later.

If this method raises an exception, it will bubble up to the frame where
`ConnectionPool.get_connection()` was called.


#### `def connection_is_closed(self, conn: Conn) -> bool`

This method is called to check if a connection is no longer able to be used.
When the pool is retrieving a connection to give to a client, this method is
called to make sure it is valid.

The return value should be `True` if the connection is _not_ valid.

If this method raises an exception, it is assumed that the connection is
invalid. The passed-in connection is dropped and a new one is retrieved. The
exception is suppressed unless it is not a `BaseException`, like
`asyncio.CancelledError`. It is the responsibility of the `ConnectionStrategy`
implementation to avoid leaking a connection in this case.


#### `async def close_connection(self, conn: Conn)`

This method is called to close a connection. This occurs when the pool has
exceeded `max_size` (i.e. it is bursting) and a connection is returned that is
no longer needed (i.e. there are no more consumers waiting for a connection).

If this method raises an exception, the connection is assumed to be closed and
the exception bubbles to the caller of `ConnectionPool.get_connection().__aexit__`
(usually an `async with` block).


## Integrations  with 3rd-party libraries

This package includes support for [`ddtrace`][ddtrace]/[`datadog`][datadog] and
for [`aioredis`][aioredis] (<2.0.0).

[ddtrace]: https://github.com/datadog/dd-trace-py
[datadog]: https://github.com/datadog/datadogpy
[aioredis]: https://github.com/aio-libs/aioredis

### `asyncio_connection_pool.contrib.datadog.ConnectionPool`

This class subclasses the `ConnectionPool` in the root of the package, and adds
a bunch of tracing, gauges, and events. The constructor, in addition to the
arguments of the base class, supports:

- Required `service_name` argument: A prefix to all of the metrics
- Optional `extra_tags` argument: Additional tags to provide to all metrics
  (strings in a `"key:value"` format)


### `asyncio_connection_pool.contrib.aioredis.RedisConnectionStrategy`

This class implements the `ConnectionStrategy` abstract methods, using
`aioredis.Redis` objects as connections. The constructor takes arbitrary
arguments and forwards them to `aioredis.create_redis`.


## How is this safe without locks?

I encourage you to read the [source](https://github.com/fellowinsights/asyncio-connection-pool/blob/master/asyncio_connection_pool/__init__.py)
to find out (it is quite well-commented). If you notice any faults in the
logic, please feel free to file an issue.
