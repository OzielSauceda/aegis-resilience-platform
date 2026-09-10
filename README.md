# Aegis

Aegis is a planned Distributed Resilience Intelligence Platform: it will observe distributed applications, detect abnormal behavior, reconstruct dependencies and failure propagation, rank likely root causes, and investigate evidence. ChaosBench will eventually supply controlled faults for evaluation.

**Batch 2 implements stateful ShopSim, the small test environment that Aegis will monitor. The Aegis platform itself is not implemented yet.**

ShopSim consists of three independent Go HTTP services. Checkout calls Inventory to reserve stock in Redis, waits for success, then calls Payment to record a fake charge in PostgreSQL. Both results return to the client. Repeating the same order does not reserve or charge again.

```mermaid
flowchart LR
    Client -->|POST /checkout| Checkout
    Checkout -->|1. POST /reserve| Inventory
    Checkout -->|2. POST /charge| Payment
    Inventory --> Redis
    Payment --> PostgreSQL
```

## Prerequisites and quick start

- Go 1.27.1 (the module requires 1.27.1 or newer in the 1.27 series).
- Docker Engine or Docker Desktop running Linux containers, with Docker Compose.
- Free host ports 8080, 8081 and 8082.

From the repository root:

```sh
go fmt ./...
go vet ./...
go test ./...
go mod tidy
go build ./...
docker compose config
docker compose build
docker compose up -d --wait
```

Go uses one module, standard-library HTTP handlers, pgx through database/sql, and go-redis. Docker packages the independently built Go executables. Multi-stage application images run as an unprivileged user. Compose limits each Go service and Redis to 128 MiB RAM, PostgreSQL to 256 MiB, and each container to 0.5 CPU. Runtime limits total 768 MiB; Docker Desktop, image builds, and the optional integration test container require additional memory.

| Service | Host URL | Internal Compose URL | Operation |
| --- | --- | --- | --- |
| Checkout | http://localhost:8080 | http://checkout:8080 | POST /checkout |
| Payment | http://localhost:8081 | http://payment:8080 | POST /charge |
| Inventory | http://localhost:8082 | http://inventory:8080 | POST /reserve |

All Go services expose `GET /healthz`, returning `{"status":"ok"}` independently of storage availability. Payment and Inventory also expose `GET /readyz`: a bounded PostgreSQL/Redis ping returns HTTP 200 with `{"status":"ready"}`, or 503 when storage cannot be reached. Checkout retains liveness only. Readiness checks connectivity, not schema integrity or available stock.

Compose waits for datastore health before starting downstream services and for their readiness before starting Checkout. Its default network supplies service DNS. Inside a container, localhost refers to that same container. Application ports are published on host loopback only; PostgreSQL (5432) and Redis (6379) are accessible only inside the Compose network.

PowerShell example:

```powershell
8080,8081,8082 | ForEach-Object { Invoke-RestMethod "http://localhost:$_/healthz" }
$body = '{"order_id":"order-123","sku":"sku-001","quantity":1,"amount_cents":2500}'
Invoke-RestMethod -Method Post -Uri http://localhost:8080/checkout -ContentType application/json -Body $body | ConvertTo-Json -Depth 4
```

POSIX shell example:

```sh
curl -sS http://localhost:8080/healthz
curl -sS http://localhost:8081/healthz
curl -sS http://localhost:8082/healthz
curl -sS http://localhost:8080/checkout -H 'Content-Type: application/json' \
  -d '{"order_id":"order-123","sku":"sku-001","quantity":1,"amount_cents":2500}'
```

Successful response (HTTP 200):

```json
{
  "order_id": "order-123",
  "status": "completed",
  "inventory": {
    "order_id": "order-123",
    "status": "reserved",
    "reservation_id": "reservation-order-123"
  },
  "payment": {
    "order_id": "order-123",
    "status": "charged",
    "payment_id": "payment-order-123"
  }
}
```

Inspect request logs and stop the environment:

```sh
docker compose logs
docker compose down
```

## API and configuration

Inventory accepts `order_id`, `sku`, and positive integer `quantity`. Payment accepts `order_id` and positive integer `amount_cents`. Checkout accepts all four fields. IDs and SKU must not be blank. Response IDs remain deterministic strings derived from the persisted order identity; response shapes are compatible with Batch 1.

Bodies are limited to 64 KiB. Malformed JSON, unknown fields, multiple JSON values, and invalid fields return HTTP 400 with `{"error":"..."}`. Unsupported methods return 405; unknown paths return 404. Direct storage operations return 503 for unavailable storage or exhausted Redis contention attempts. Inventory returns 404 for unknown SKUs and 409 for insufficient stock. Both downstreams return 409 for changed details on an existing order. Checkout preserves downstream 409 responses; other downstream non-success responses, connection failures, and invalid success payloads map to 502. HTTP timeouts map to 504. Error bodies omit internal details; failure logs include order IDs.

Checkout uses a two-second timeout per downstream call, propagates request cancellation, and does not follow redirects or retry calls. Its downstream origins are required environment variables:
- `INVENTORY_URL`, for example `http://localhost:8082`.
- `PAYMENT_URL`, for example `http://localhost:8081`.

Payment requires `DATABASE_URL`; Inventory requires `REDIS_URL`. Compose supplies `postgres://shopsim:shopsim-local@postgres:5432/shopsim?sslmode=disable&connect_timeout=1` and `redis://redis:6379/0`. The database credentials are local development defaults, not production credentials. Payment has at most four connections (two idle); Redis has at most four active connections. Each HTTP storage operation has a one-second context deadline, inside Checkout's two-second downstream deadline.

All services support `LISTEN_ADDR` (default `:8080`). To run the Go services outside Docker, first provide reachable PostgreSQL and Redis instances and apply `shopsim/payment/init.sql` to PostgreSQL. The default Compose configuration intentionally does not publish datastore ports. With separately provisioned local datastores, open three terminals at the repository root. In PowerShell:

```powershell
# Terminal 1
$env:LISTEN_ADDR = ':8082'
$env:REDIS_URL = 'redis://localhost:6379/0'
go run ./shopsim/inventory
# Terminal 2
$env:LISTEN_ADDR = ':8081'
$env:DATABASE_URL = 'postgres://shopsim:shopsim-local@localhost:5432/shopsim?sslmode=disable&connect_timeout=1'
go run ./shopsim/payment
# Terminal 3
$env:LISTEN_ADDR = ':8080'
$env:INVENTORY_URL = 'http://localhost:8082'
$env:PAYMENT_URL = 'http://localhost:8081'
go run ./shopsim/checkout
```

## State and idempotency

PostgreSQL stores `payments(order_id PRIMARY KEY, amount_cents, status, created_at)`. SQL constraints require a positive amount and `charged` status. Payment inserts with `ON CONFLICT DO NOTHING`; when an order exists, a separate read checks its amount. Identical requests return the same result without updating the row or timestamp. A changed amount returns 409. The unique key also handles concurrent requests. No ORM or migration framework is used.

Redis stores integer `stock:<sku>` keys and `reservation:<order_id>` hashes containing `sku` and `quantity`, without expiration. On Inventory startup, SETNX seeds only missing stock keys: `sku-001=100`, `sku-002=50`, `sku-003=25`. Restarting Inventory preserves existing quantities, including zero.

Inventory watches both the stock key and reservation key, checks an existing reservation first, then checks stock and commits both writes with MULTI/EXEC. Matching replays never decrement again; changed SKU or quantity conflicts. WATCH conflicts retry at most eight times with a short bounded delay, all within the request deadline. Other failures are not retried because the commit outcome may be uncertain. See [Redis's transaction documentation](https://redis.io/docs/latest/develop/clients/go/transpipe/).

Named volumes `postgres-data` and `redis-data` preserve state across normal shutdowns/restarts. Redis enables AOF with an every-second fsync and no key eviction. Abrupt host failure can still lose roughly a second of Redis writes. Redis transactions isolate these commands but do not offer SQL-style rollback on arbitrary runtime failures. These guarantees assume the application owns its keys and sufficient storage is available.

PostgreSQL runs `init.sql` only when initializing an empty data volume. Editing that file does not migrate an existing database; apply deliberate SQL changes for later schema revisions. `docker compose down` preserves both volumes. Use `docker compose down --volumes` only for an intentional reset that deletes all ShopSim state.

Inspect state after the sample checkout:

```sh
docker compose exec -T postgres psql -U shopsim -d shopsim -c "SELECT * FROM payments WHERE order_id = 'order-123';"
docker compose exec -T redis redis-cli GET stock:sku-001
docker compose exec -T redis redis-cli HGETALL reservation:order-123
```

Repeat the sample request: one payment row should remain and stock should fall only once. On a fresh volume, its quantity of one leaves `sku-001` at 99.

## Tests and repeatable stateful validation

`go test ./...` runs the existing HTTP tests plus new handler tests. Real-store tests skip unless `TEST_DATABASE_URL` and `TEST_REDIS_URL` are set to reachable test datastores with the schema initialized. These tests use unique keys/rows and clean up only their own data. They exercise real concurrent inserts/reservations, replay, conflicts, insufficient stock, and cancellation.

On Windows, this script runs the Go storage tests inside a temporary build-stage container on the Compose network, then tests real checkout replay/conflicts, direct database state, storage outages, partial failure/recovery, and application/datastore restarts:

```powershell
docker compose -p aegis-batch2-validation build
.\scripts\validate-stateful.ps1
```

The script uses the separate `aegis-batch2-validation` project, requires free ports 8080–8082, and shuts it down in a finally block. Its named volumes and the end-to-end orders are retained for inspection; each run consumes two units of `sku-001` and one of `sku-002`. Run against an intentional clean test project if that stock is depleted. The test runner has a temporary 1 GiB container limit, a 512 MiB Go soft memory limit, and serial package compilation (the compiler exceeded an initial 512 MiB container limit). For other shells, set the two test URLs and run `go test -v ./shopsim/payment ./shopsim/inventory` against accessible datastores.

## Boundaries and next stages

Payment failure occurs after a real stock reservation. Checkout logs the order and retained reservation and returns an error; there is no rollback, reservation release/expiry, or compensation. An exact manual replay can complete the payment after recovery without reserving twice. A timeout does not prove an operation never committed. Idempotency is per downstream service; PostgreSQL and Redis do not share a transaction, and losing/restoring one store independently can leave them inconsistent. This is a deliberate experimental failure scenario, not a production ecommerce backend.

The request's downstream calls are deliberately sequential; the standard HTTP server can still serve independent clients concurrently.

OpenTelemetry/OTLP, Collector, telemetry analytics storage, messaging, ChaosBench, analysis/ML, root-cause ranking, AI/LLMs, control plane, frontend, authentication, HTTP retries, circuit breakers, compensation, Kubernetes, Terraform and cloud deployment remain outside this batch. Redis is an inventory store here to provide a distinct stateful dependency and failure mode for experiments; it is not a universal production ecommerce recommendation.

See [architecture v0](docs/architecture/architecture-v0.md) and the [ADRs](docs/adr).
