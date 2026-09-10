# 0005 — Use PostgreSQL for the ShopSim payment ledger

## Status

Accepted

## Context

ShopSim needs a real stateful dependency whose latency, connectivity and persistence can later be observed and disrupted. Mock charges need a simple durable identity so replaying an order cannot create duplicate ledger entries.

## Decision

Use PostgreSQL with a named Compose volume and a small first-run initialization SQL file. Use explicit SQL through database/sql and pgx, with four maximum connections and context deadlines. The order ID is the primary key; amounts must be positive and status is currently always charged.

INSERT ON CONFLICT DO NOTHING creates at most one row. A separate read after a conflict sees the committed winner and compares amounts. Identical amounts return the existing result; changed amounts return HTTP 409 without modifying the ledger. PostgreSQL uniqueness handles concurrent requests. See the [pgx database/sql adapter](https://github.com/jackc/pgx/wiki/Getting-started-with-pgx-through-database-sql).

## Consequences

Relational constraints provide a clear invariant and PostgreSQL introduces a useful database failure mode. One named volume survives normal container recreation. The service remains fake payment processing and never contacts an external provider.

Initialization SQL is not a migration system; future schema changes require deliberate SQL changes against existing volumes. A timed-out write may have committed, so callers must reuse the same order identity when manually retrying. Per-service idempotency does not undo an Inventory reservation if Payment fails. This batch adds neither a distributed transaction nor compensation. The local database user/password and disabled TLS are Compose development defaults only.
