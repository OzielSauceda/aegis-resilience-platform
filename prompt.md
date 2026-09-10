# Aegis — Implementation Batch 2
# Stateful ShopSim Dependencies: PostgreSQL + Redis

You are working in the Aegis repository after successful completion of
Implementation Batch 1.

Before modifying anything:

1. Inspect the repository.
2. Inspect the current Git branch.
3. Read the existing README.
4. Read docs/architecture/architecture-v0.md.
5. Read all existing ADRs.
6. Inspect the three ShopSim services and their tests.
7. Inspect docker-compose.yml.
8. Run or inspect the current tests enough to understand the known-good baseline.

Do not assume the repository matches an earlier prompt exactly.
The existing repository is the source of truth.

Do NOT commit, push, merge, rebase, create a PR, or change branches.
Leave all changes in the working tree for human review.

============================================================
1. PROJECT CONTEXT
============================================================

Aegis is a Distributed Resilience Intelligence Platform.

The current implementation does NOT yet contain Aegis itself.

It currently contains ShopSim, a controlled distributed application
that will eventually be monitored and deliberately broken by Aegis.

Current ShopSim topology:

Client
  |
  | HTTP/JSON
  v
Checkout
  |
  +----> Inventory
  |
  +----> Payment

All three are independently executable Go services running in
separate Docker containers and communicating synchronously over HTTP.

Current behavior is intentionally stateless:

Inventory returns a deterministic mock reservation.

Payment returns a deterministic mock charge.

Batch 1 already established the real network/service boundaries.

============================================================
2. BATCH 2 OBJECTIVE
============================================================

Convert the downstream ShopSim services from stateless mocks into
small but real stateful services.

Target architecture:

Client
  |
  v
Checkout
  |
  +----> Inventory ----> Redis
  |
  +----> Payment ------> PostgreSQL

Inventory must use Redis for stock/reservation state.

Payment must use PostgreSQL for a mock payment ledger.

The purpose is NOT to create a production ecommerce system.

The purpose is to introduce realistic external stateful dependencies
that can later fail, become slow, lose connectivity, and generate
interesting telemetry for Aegis.

============================================================
3. EXISTING DECISIONS TO PRESERVE
============================================================

Preserve the following unless a genuine technical blocker is found:

- One monorepo.
- ShopSim services remain Go.
- One Go module unless the repository strongly indicates otherwise.
- Synchronous HTTP/JSON communication.
- Checkout remains the orchestrator.
- Inventory is called before Payment.
- Docker Compose remains the local runtime.
- Local development must remain practical on a 16 GB RAM laptop.
- Keep resource usage intentionally modest.
- Prefer understandable code over elaborate abstractions.
- Do not introduce cloud services.

Do not introduce new languages for ShopSim.

============================================================
4. PAYMENT SERVICE — POSTGRESQL
============================================================

Replace Payment's purely deterministic mock behavior with a small
persistent mock payment ledger stored in PostgreSQL.

This is still fake payment processing. No external payment provider
should be contacted.

Use PostgreSQL as a Docker Compose service.

Use a named volume so payment data can survive normal container
restarts.

Use a small initialization SQL file or similarly lightweight mechanism
to create the required schema.

Do NOT introduce a heavy migration framework yet unless the repository
already requires one for a compelling reason.

A minimal schema should represent at least:

- order_id
- amount_cents
- payment/charge status
- created_at

Use order_id as the idempotency identity unless there is a clearly
better simple design.

A reasonable relational invariant is:

one payment record per order_id

The database should enforce that invariant, preferably through a
primary key or unique constraint rather than relying only on Go code.

Payment behavior:

POST /charge

New order_id:
    create a mock successful charge in PostgreSQL
    return success

Repeated order_id with the SAME amount:
    do NOT create another charge
    return the already-existing successful result

Repeated order_id with a DIFFERENT amount:
    return an HTTP conflict response
    do not modify the existing record

This gives the service basic idempotent behavior.

Use explicit SQL.

Do not introduce an ORM.

Using database/sql plus an appropriate PostgreSQL driver such as pgx
is acceptable.

Keep connection-pool settings modest for the 16 GB local development
constraint.

Use context-aware database operations and reasonable timeouts.

If PostgreSQL becomes unavailable while Payment is running, Payment
must return a controlled service error rather than panic.

============================================================
5. INVENTORY SERVICE — REDIS
============================================================

Replace Inventory's purely deterministic mock reservation behavior
with real state stored in Redis.

Use Redis as a Docker Compose service.

Enable reasonable local persistence, such as Redis AOF, and use a
named Docker volume so state can survive normal container restarts.

Inventory should model two concepts:

1. available stock for a SKU
2. an order reservation

A reasonable key design could conceptually resemble:

stock:<sku>

reservation:<order_id>

but choose the exact representation based on clean Redis usage.

Provide a very small deterministic initial inventory dataset.

For example, a few known ShopSim SKUs with fixed starting quantities.

Initialize them only when appropriate; restarting Inventory must not
silently reset existing stock.

POST /reserve must:

- validate order_id
- validate SKU
- validate positive quantity
- check available stock
- reserve the quantity
- decrement available stock
- remember which order created the reservation

Repeated order_id with the SAME SKU and quantity:
    do not decrement stock again
    return the existing reservation

Repeated order_id with DIFFERENT reservation details:
    return an HTTP conflict response

Insufficient stock:
    return an appropriate non-success HTTP response
    do not allow stock to become negative

============================================================
6. REDIS ATOMICITY
============================================================

Inventory's operation:

check stock
+
create reservation
+
decrement stock

must not contain an obvious race condition.

Do not implement:

GET stock
then
SET stock

as unrelated commands with no concurrency protection.

Prefer an understandable Redis-native atomic mechanism.

Using WATCH / MULTI / EXEC through the Go Redis client is preferred if
it produces clean code.

A small Lua script is acceptable only if it produces a significantly
cleaner and more correct solution; do not introduce Lua casually.

If optimistic concurrency/retry behavior is required by Redis
transactions, keep retry limits bounded and document the reasoning.

The goal is correct reservation behavior without overengineering.

============================================================
7. REDIS AND POSTGRESQL CLIENTS
============================================================

Use mature, commonly used Go clients.

Reasonable choices include:

PostgreSQL:
github.com/jackc/pgx/v5

Redis:
github.com/redis/go-redis/v9

Do not add unnecessary database abstraction frameworks.

Run go mod tidy after dependency changes.

============================================================
8. LIVENESS AND READINESS
============================================================

Preserve the existing:

GET /healthz

Semantics:

/healthz answers whether the service process is alive.

Introduce:

GET /readyz

where useful.

Payment /readyz should report ready only when it can reach PostgreSQL.

Inventory /readyz should report ready only when it can reach Redis.

Checkout readiness may check whether its required downstream services
are available if this can be implemented cleanly without creating
unnecessary complexity.

Do not change /healthz into a recursive dependency checker.

We want to preserve the conceptual difference:

liveness = process is alive

readiness = service can currently perform its required work

Docker Compose health checks may use readiness where appropriate.

============================================================
9. DOCKER COMPOSE
============================================================

Extend the existing Compose configuration.

Add:

PostgreSQL
Redis

Keep the three existing ShopSim services.

Configure service DNS names through Docker Compose rather than
hard-coded IP addresses.

Payment should receive its database connection settings through
environment configuration.

Inventory should receive its Redis connection settings through
environment configuration.

Use Compose dependency/health behavior so services do not blindly
assume their data stores are immediately usable.

Use named volumes for state.

Keep local resource limits reasonable.

Do not add:

Kafka
Redpanda
ClickHouse
OpenTelemetry Collector
Kubernetes
Terraform

============================================================
10. CHECKOUT BEHAVIOR
============================================================

Preserve the current sequential orchestration:

Checkout
  -> Inventory reservation
  -> Payment charge
  -> success response

Do not add concurrency yet.

Do not add automatic HTTP retries yet.

Do not add a circuit breaker yet.

IMPORTANT:

Once Inventory becomes stateful, this workflow can produce a partial
failure:

Inventory reservation succeeds
Payment fails

This batch does NOT need to implement a distributed compensation
workflow.

Do not silently add a Saga framework or elaborate rollback mechanism.

Instead:

- handle the error cleanly
- log enough context to identify the order and failure
- document the known partial-failure behavior
- preserve it as an explicit architectural limitation for a future
  deliberate design decision

This limitation will eventually provide useful distributed-system
failure scenarios for Aegis.

============================================================
11. LOGGING
============================================================

Keep logging simple and consistent.

Important operations should include enough context to correlate events,
especially order_id and relevant service information.

Examples worth logging:

- reservation created
- idempotent reservation replay
- insufficient inventory
- payment created
- idempotent payment replay
- Redis failure
- PostgreSQL failure
- Checkout downstream failure
- partial checkout failure

Do not create a custom logging framework.

Do not add OpenTelemetry logging yet.

============================================================
12. TESTING
============================================================

Preserve all existing passing tests.

Add focused tests for the new behavior.

Payment should test at least the important logical cases:

- valid new charge
- repeated identical charge is idempotent
- same order_id with different amount conflicts
- invalid request
- storage failure handled cleanly

Inventory should test at least:

- valid reservation
- stock is decreased
- repeated identical reservation is idempotent
- repeated reservation does not decrease stock twice
- same order_id with different request conflicts
- insufficient stock
- invalid request
- storage failure handled cleanly

Avoid unnecessarily complex mocking frameworks.

If small store interfaces are now justified to make handlers testable,
that is acceptable, but keep them narrow and purposeful.

============================================================
13. END-TO-END VALIDATION
============================================================

After implementation, validate as much as the environment supports.

Run:

go fmt ./...

go vet ./...

go test ./...

go mod tidy

docker compose config

docker compose build

Start from a clean test environment when appropriate.

Bring the Compose stack up and verify:

PostgreSQL healthy
Redis healthy
Payment ready
Inventory ready
Checkout healthy/ready as appropriate

Then perform a real checkout request.

Verify:

Client
 -> Checkout
 -> Inventory
 -> Redis

and:

Checkout
 -> Payment
 -> PostgreSQL

successfully occur.

Repeat the EXACT same checkout request.

Verify it does NOT:

- create a second payment record
- decrement inventory a second time

Verify database state directly where practical.

For PostgreSQL, verify only one payment row exists for the order.

For Redis, verify the reservation exists and available stock was
decremented exactly once.

Then test at least one conflict case.

For example:

same order_id
different amount

or:

same order_id
different quantity

and confirm it fails without corrupting existing state.

Where practical, restart the application containers and verify
PostgreSQL/Redis-backed state survives.

Finally:

docker compose down

Do not destroy persistent volumes unless intentionally performing a
clean reset.

Clearly distinguish tests that were actually run from tests that were
not possible to run.

============================================================
14. DOCUMENTATION
============================================================

Update README.md to describe Batch 2 accurately.

Update docs/architecture/architecture-v0.md.

The architecture should now show:

Client
  |
  v
Checkout
  |
  +----> Inventory ----> Redis
  |
  +----> Payment ------> PostgreSQL

Document:

- what state now exists
- why PostgreSQL is used behind Payment
- why Redis is used behind Inventory
- basic idempotency semantics
- known partial-failure behavior
- liveness vs readiness
- what remains intentionally unimplemented

Do not claim Aegis monitoring functionality exists yet.

Create ADRs for the new major architecture decisions.

Suggested:

0005 — Use PostgreSQL for the ShopSim payment ledger

0006 — Use Redis for ShopSim inventory/reservation state

Each ADR should contain:

Title
Status
Context
Decision
Consequences

Explain the tradeoffs.

For Redis specifically, be transparent that using Redis as the
ShopSim inventory state store is primarily useful for creating a
distinct stateful dependency and failure mode for Aegis experiments;
it should not be presented as universal production ecommerce
architecture.

============================================================
15. EXPLICIT NON-GOALS
============================================================

DO NOT implement:

OpenTelemetry
OTLP
OpenTelemetry Collector
ClickHouse
Kafka
Redpanda
ChaosBench
anomaly detection
machine learning
root-cause analysis
AI investigation
LLMs
frontend
Aegis control plane
Kubernetes
Terraform
cloud deployment
authentication
real financial/payment processing
concurrent Checkout fan-out
automatic HTTP retries
circuit breakers
distributed Saga framework
automatic compensation

Those belong to later deliberate batches.

============================================================
16. CODE QUALITY
============================================================

The codebase is still intentionally small.

Refactor files if Batch 1 main.go files would otherwise become
unreasonably large.

Good separations might include:

HTTP handling
configuration
storage/repository behavior

but only introduce files/abstractions that now have a real purpose.

Prefer:

clear Go code
explicit errors
context-aware operations
direct SQL
small interfaces when justified
bounded timeouts
good request validation

Avoid:

ORMs
dependency injection frameworks
generic repository frameworks
premature shared infrastructure
unnecessary packages
large architecture rewrites

============================================================
17. GIT SAFETY
============================================================

Do NOT:

git commit
git push
git merge
git rebase
create a tag
create a PR
switch branches

Leave all implementation changes in the current working tree.

============================================================
18. REQUIRED FINAL REPORT
============================================================

When finished, report:

1. Files created and modified.
2. Any new Go dependencies and why each was needed.
3. Final request/data flow.
4. PostgreSQL schema and how Payment uses it.
5. Redis key/state design and how Inventory uses it.
6. How idempotency works in Payment.
7. How idempotency and atomicity work in Inventory.
8. How /healthz and /readyz differ.
9. Docker Compose changes.
10. Tests and validation commands actually run.
11. Exact validation results.
12. Any deviations from this prompt and why.
13. Known limitations or technical debt.
14. Output of git status --short.
15. Recommended human review order.

Do not claim anything passed unless it was actually executed and
verified.

The goal of Batch 2 is:

ShopSim evolves from:

three distributed stateless Go services

into:

three distributed Go services with real stateful infrastructure
dependencies that can later become targets for observability,
fault injection, and Aegis incident analysis.

Build that coherent milestone and stop there.