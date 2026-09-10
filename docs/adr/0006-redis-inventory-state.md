# 0006 — Use Redis for ShopSim inventory and reservation state

## Status

Accepted

## Context

ShopSim needs a second stateful dependency with different storage and failure behavior from PostgreSQL. Inventory must reserve stock without overselling and must handle repeated requests without decrementing twice. Redis here is an experimental dependency for Aegis, not a claim about universal production ecommerce architecture.

## Decision

Use go-redis with integer stock keys and order-keyed reservation hashes. Seed three small SKU quantities using SETNX on Inventory startup. Store keys without expiration and disable eviction. Enable Redis AOF with every-second fsync and a named Compose volume.

Use WATCH on both the reservation and stock keys, validate existing reservations/stock, and write both changes through MULTI/EXEC. Retry only known optimistic-lock aborts, at most eight attempts with delays of 5, 10, up to 35 milliseconds within a one-second request deadline. Exhaustion returns 503. Other errors are not retried because a disconnected client may not know whether EXEC committed. No Lua is needed. The approach follows [Redis's Go transaction guidance](https://redis.io/docs/latest/develop/clients/go/transpipe/).

## Consequences

Competing requests cannot both spend the same observed stock. Replays with matching SKU and quantity succeed without another decrement; changed details conflict. WATCH may abort under contention, so bounded retries trade availability under heavy load for predictable latency. Redis is single-instance local infrastructure; cluster-specific key placement is outside scope.

Restarting Inventory does not reset existing stock. Normal datastore restarts preserve AOF state, but an abrupt host failure can lose roughly a second of writes. Redis transactions do not offer SQL rollback for arbitrary runtime errors; the app owns key types and expects adequate storage. There is no reservation expiration, release, cross-store transaction, or compensation. A payment failure can leave a reservation indefinitely, an intentional future fault-analysis scenario.
