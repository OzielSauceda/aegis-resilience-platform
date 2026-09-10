# 0003 — Use synchronous HTTP/JSON for initial ShopSim communication

## Status

Accepted

## Context

Batch 1 should make distributed request flow observable and easy to inspect without adding protocol tooling or messaging infrastructure.

## Decision

Checkout calls Inventory and then Payment synchronously over HTTP with JSON, using runtime-configured origins and explicit timeouts.

## Consequences

Requests are easy to inspect with common tools and test with httptest. Sequential latency is additive and downstream failure affects checkout. JSON contracts require explicit validation. No transaction, retry, rollback or asynchronous delivery guarantee is implied.

