# Batch 3 implementation report — ShopSim distributed tracing

Date: 2026-09-10. Implementation, tracing validation, and the full stateful regression are complete and passed.

## 1. Repository and Git state

- Branch: `feature/shopsim-observability`.
- Starting commit: `385a183a4e76930ee7401e6b3013224743368e37`.
- `HEAD`, local `main`, and the inspected `origin/main` reference were identical. The commit merges the completed Batch 2 implementation (`e845467`). No branch repair was needed.
- Starting working tree: only the user-supplied `prompt.md` was modified. Thus it was not literally clean; implementation files were clean. The prompt was preserved without agent edits.
- No commits, pushes, merges, rebases, tags, PRs, or branch changes were performed.

## 2. Files changed

| File | Change and architectural purpose |
| --- | --- |
| `shopsim/internal/telemetry/telemetry.go` | New shared SDK/resource/exporter/propagator initialization and sanitized datastore error recording. Reuses the existing one-module internal-package boundary. |
| `shopsim/internal/telemetry/telemetry_test.go` | Tests deterministic sampling, service resource identity, propagation setup, and credential-free error events without a telemetry backend. |
| `shopsim/internal/httpio/httpio.go` | Instruments only application routes, uses fixed HTTP span names and route attributes, initializes each service's SDK, drains requests on signals, and flushes tracing with a bounded independent context. |
| `shopsim/internal/httpio/tracing_test.go` | Verifies probe exclusion, incoming W3C parents, and business-conflict versus HTTP 503 span status. |
| `shopsim/checkout/main.go` | Wraps the HTTP transport while preserving timeouts/redirect policy; attaches business attributes to the root and adds a retained-reservation event on Payment failure; handles errors returned by shared server startup. |
| `shopsim/checkout/tracing_test.go` | Uses real httptest HTTP boundaries to assert a common Trace ID, server/client kinds and parent relationships, and preservation of a supplied transport. |
| `shopsim/inventory/store.go` | Adds one `redis.reserve` CLIENT span around the original reservation transaction with outcome/attempt attributes and sanitized infrastructure failures. |
| `shopsim/payment/store.go` | Adds one `postgresql.charge` CLIENT span around the original SQL/idempotency operation, with outcome attributes and sanitized failures. |
| `shopsim/inventory/main.go` | Handles the shared HTTP runner's returned error; retains Redis configuration, seeding, and business handler logic. |
| `shopsim/payment/main.go` | Handles the shared HTTP runner's returned error; retains PostgreSQL configuration and business handler logic. |
| `shopsim/checkout/main_test.go`, `shopsim/inventory/main_test.go`, `shopsim/inventory/store_test.go`, `shopsim/inventory/store_integration_test.go`, `shopsim/payment/main_test.go`, `shopsim/payment/store_test.go`, `shopsim/payment/store_integration_test.go` | Existing tests were processed by required `go fmt ./...`; Git status reports their line-ending refresh, but `git diff` shows no semantic changes. |
| `go.mod`, `go.sum` | Pin the tracing SDK, HTTP instrumentation, exporter, APIs, and required transitive modules. |
| `observability/otel-collector.yaml` | New traces-only Collector receive/process/export configuration and health endpoint. |
| `observability/jaeger.yaml` | New minimal Jaeger v2 in-memory backend, OTLP receiver, and query/UI configuration. |
| `docker-compose.yml` | Adds two pinned observability images, endpoint environment variables, loopback ports, and resource limits; adds no business dependency on telemetry. |
| `scripts/validation-helpers.ps1` | Extracts existing Compose/assertion/request helpers for both validation scripts. |
| `scripts/validate-stateful.ps1` | Sources the shared helper; preserves the existing Batch 2 checks. |
| `scripts/validate-observability.ps1` | New automated Jaeger trace, failure/recovery, probe-noise, and telemetry-outage validation with saved evidence. |
| `.gitignore` | Ignores generated `validation-artifacts/` JSON and logs. |
| `README.md` | Updates the batch description, ports, resource totals, tracing overview, and documentation links. |
| `docs/architecture/architecture-v0.md` | Separates implemented tracing from future metrics/logs/analysis architecture. |
| `docs/observability/tracing.md` | New developer guide: concepts, configuration, lifecycle, UI usage, failure experiments, validation, and limitations. |
| `docs/adr/0007-tracing-first-observability.md` | Records tracing-first scope, shared initialization, core Collector, transient Jaeger, and availability tradeoffs. |
| `docs/reports/batch-3.md` | This saved report and representative trace evidence. |

Existing Batch 1/2 test cases and business logic remain in place. `go fmt ./...` also refreshed some existing test files' filesystem line endings without semantic diffs. The user-authored `prompt.md` appears in Git output but is not an implementation change.

## 3. Final telemetry architecture

```text
Business flow (sequential):
Client -> Checkout -> Inventory -> Redis
                 \-> Payment   -> PostgreSQL

Telemetry flow:
checkout SDK ---+
inventory SDK -+-- OTLP/gRPC --> otel-collector --> OTLP/gRPC --> Jaeger v2
payment SDK ---+                core 0.160.0                    2.20.0
                               memory limiter + batch         in-memory traces
                               basic debug summaries          UI :16686
```

## 4. Trace lifecycle

1. The application-route HTTP wrapper starts `POST /checkout` as a SERVER span. It records the order ID, SKU, quantity and amount after validation.
2. Checkout makes its normal Inventory request through an instrumented transport, starting an HTTP CLIENT child.
3. Inventory extracts that parent context and creates `POST /reserve` SERVER, then `redis.reserve` CLIENT around its lookup/WATCH/MULTI/EXEC operation.
4. Once Inventory succeeds, Checkout similarly creates its Payment HTTP CLIENT child. Payment creates `POST /charge` SERVER and `postgresql.charge` CLIENT around the SQL/idempotency operation.
5. Spans end as operations finish. HTTP spans capture method/status/latency; storage spans capture created/replayed/conflict/failure outcomes. The response behavior is unchanged.
6. Each SDK asynchronously exports completed spans in bounded batches to Collector. Collector memory-limits/batches and forwards them to Jaeger, where their Trace ID and parent IDs join them into one causal tree.

## 5. Context propagation

The shared initializer installs the standard composite `TraceContext` and `Baggage` propagators. `otelhttp.NewTransport` injects the current client span into standard HTTP tracing headers; `otelhttp.NewHandler` extracts those headers into the receiving request's context. Storage starts its child using that same request context. No IDs or custom tracing headers are constructed by ShopSim.

An HTTP boundary changes the Span ID but retains the Trace ID. Manual replay is a new HTTP request and therefore usually a new trace; `shopsim.order_id` connects the separate attempts as a business identity.

## 6. Important code to study

- `telemetry.Init`: `resource.NewSchemaless`, `sdktrace.NewTracerProvider`, `ParentBased(AlwaysSample())`, `WithBatcher`, and `otlptracegrpc.New`.
- `httpio.Route`: fixed-name `otelhttp.NewHandler` around only the application endpoint, explicit `http.route`, and a no-op meter provider.
- `newHTTPClient`: wraps the supplied transport with `otelhttp.NewTransport` and keeps the two-second timeout/no-redirect policy.
- `redisStore.Reserve` and `postgresStore.Charge`: `Tracer.Start`, `SpanKindClient`, deferred outcome/error recording and `span.End`, while preserving the storage algorithm.
- `telemetry.StorageError`: classifies errors without copying credential-bearing driver messages into traces.
- `httpio.Run`: initializes tracing, listens for SIGTERM/Ctrl+C, allows five seconds of HTTP draining, then uses a separate three-second context for SDK shutdown/flush.
- `Verify-Trace` in the new validation script: checks actual Jaeger records rather than inferring success from application responses alone.

The existing module layout allowed shared initialization without new modules or a telemetry framework. HTTP wrappers use the normal global OpenTelemetry provider/propagator mechanism; tests replace and restore those globals without parallel mutation.

## 7. New direct dependencies

| Module | Version | Reason |
| --- | --- | --- |
| `go.opentelemetry.io/otel` | 1.46.0 | Global tracing/propagation APIs, attributes and status codes. |
| `go.opentelemetry.io/otel/trace` | 1.46.0 | Span/context APIs and span kinds. |
| `go.opentelemetry.io/otel/sdk` | 1.46.0 | Resources, sampling, batch processor, TracerProvider, and in-memory test exporters. |
| `go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc` | 1.46.0 | OTLP trace delivery over gRPC. |
| `go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp` | 0.71.0 | Standard HTTP server/client tracing and header propagation. |
| `go.opentelemetry.io/otel/metric` | 1.46.0 | Explicit no-op meter provider for HTTP instrumentation; no metric SDK/export pipeline is configured. |

gRPC, protobuf, OTLP protocol definitions, HTTP response-writer instrumentation, logging adapters and backoff modules are transitive dependencies. `x/sync`, `x/sys`, and `x/text` advanced to versions required by the new modules. pgx and go-redis versions did not change. `go mod tidy` completed and the resulting module files were inspected.

## 8. Collector configuration

The official core distribution contains every required component; contrib was unnecessary. An OTLP receiver accepts gRPC on 4317. A memory limiter (96 MiB with a 24 MiB spike allowance) runs before a batch processor (one-second timeout, 64-span target, 128-span maximum). `otlp_grpc/jaeger` exports to `jaeger:4317` with a bounded queue and up to 30 seconds of background retry. The basic debug exporter logs sampled summary counts. The health extension listens on 13133. Only a traces pipeline exists; internal metrics are disabled.

## 9. Docker changes

Collector is pinned to `otel/opentelemetry-collector:0.160.0`; Jaeger to `cr.jaegertracing.io/jaegertracing/jaeger:2.20.0`. Both read explicit mounted YAML. Jaeger stores at most 2,000 traces in memory and exposes its UI/API at `http://localhost:16686`. Collector exposes OTLP 4317 and health 13133 on loopback. Jaeger's OTLP endpoint is internal to the Compose network.

Applications retain only their business dependencies in Compose. No application readiness probe calls Collector or Jaeger. Their startup/outage independence was tested. Existing PostgreSQL/Redis named volumes are preserved. The shell-free Collector image uses a real external health probe in the script instead of a fabricated shell-based container health check.

## 10. Validation performed

Commands executed at the repository root included:

```powershell
git status
git branch --show-current
git log --oneline --decorate -n 10
git rev-parse HEAD main
git merge-base HEAD main
go test ./...  # baseline
go get go.opentelemetry.io/otel go.opentelemetry.io/otel/sdk go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp
go mod tidy
go fmt ./...
go vet ./...
go test ./...
docker compose config --quiet
docker compose -p aegis-batch3-validation pull otel-collector jaeger
docker compose -p aegis-batch3-validation build
docker compose -p aegis-batch3-validation up -d --wait
docker compose -p aegis-batch3-validation restart otel-collector
.\scripts\validate-observability.ps1
.\scripts\validate-stateful.ps1 -Project aegis-batch3-validation
git diff --check
```

The scripts execute direct PostgreSQL/Redis state inspection, HTTP requests, ordinary Docker stop/start/restart operations, and final `docker compose ... down` without deleting volumes. The stateful script also builds the existing payment Dockerfile's `build` target and executes:

```text
docker run --rm --network aegis-batch3-validation_default --memory 1g --cpus 1
  -e GOMEMLIMIT=512MiB
  -e TEST_DATABASE_URL=postgres://shopsim:shopsim-local@postgres:5432/shopsim?sslmode=disable&connect_timeout=1
  -e TEST_REDIS_URL=redis://redis:6379/0
  aegis-shopsim-storage-tests go test -p 1 -v -count=1 ./shopsim/payment ./shopsim/inventory
```

Final local `go test ./...` passed all five packages: checkout (0.245s), httpio (0.121s), telemetry (cached), inventory (0.110s), and payment (0.108s). Real-store tests skip in local runs without connection variables; their separate Docker execution is recorded in section 13.

Development checks caught two version-specific HTTP instrumentation issues: the installed version removed `WithRouteTag`, and its default formatter ignored the supplied operation name. The implementation now sets the fixed route attribute directly and supplies an explicit formatter. Formatting, vet, and all local tests passed after those corrections. Collector logs identified a deprecated exporter alias; it was replaced by `otlp_grpc` and the final configuration passed real trace validation.

All observability script checks passed with exit code 0: seven containers started, connected success/failure/recovery traces were inspected, Collector receipt was confirmed, probe spans were absent, and Collector/Jaeger outages did not break application behavior. No browser-based manual UI inspection was performed; trace semantics were checked through the real Jaeger JSON API, and representative raw records were inspected. Exact manual UI instructions are in the tracing guide.

## 11. Distributed trace evidence

Success Trace ID: `5b8cf0809ddfa2231f7aaee20dfa5c2f`. All seven spans had that Trace ID. All resources had namespace `shopsim` and the expected service identity.

| Service | Span name | Kind | Span ID | Parent Span ID | Duration (microseconds) |
| --- | --- | --- | --- | --- | ---: |
| checkout | POST /checkout | SERVER | b06c5cea9b7e4c74 | none | 7228 |
| checkout | HTTP POST (Inventory) | CLIENT | 287a48fd284bdb62 | b06c5cea9b7e4c74 | 2917 |
| inventory | POST /reserve | SERVER | 9eb5978490cfedc6 | 287a48fd284bdb62 | 1502 |
| inventory | redis.reserve | CLIENT | 8ae72d5d1110f3a4 | 9eb5978490cfedc6 | 1220 |
| checkout | HTTP POST (Payment) | CLIENT | a8cc9ab1954184cf | b06c5cea9b7e4c74 | 3095 |
| payment | POST /charge | SERVER | fd8e08123b725d26 | a8cc9ab1954184cf | 1793 |
| payment | postgresql.charge | CLIENT | 00166694536a4781 | fd8e08123b725d26 | 1611 |

Both storage outcomes were `created`; all HTTP spans reported POST and status 200. The Collector debug summary independently reported three resource spans and seven spans for the initial real checkout.

PostgreSQL outage Trace ID: `e154c7ae8e226ce55e56eeced0c5ae1f`.

- `postgresql.charge` span `eec89461c7ace97f` was a child of Payment SERVER `21a54b6d5bd90f73` and lasted 195 microseconds in this connection-refusal example.
- Its tags included `error=true`, `otel.status_code=ERROR`, `error.type=unavailable`, and `shopsim.outcome=storage_error`.
- Its exception event message was `postgresql operation unavailable`; no connection string or password appeared.
- Payment SERVER and its Checkout HTTP CLIENT returned 503. Checkout SERVER `fcaeec4e27b512dd` returned 502 and carried the reservation-retained event. Inventory/Redis remained successful in that same trace.

Recovery Trace ID: `4cf9cbc2f2722890bfb33653aa1e4e49`. Redis reported `replayed`, PostgreSQL `created`, and HTTP returned 200. Stock remained at 97 across that manual replay, and the recovered order had exactly one payment row.

Trace after restoring observability: `373aa85cc0141906e90c068ede9c3770`, again containing the complete seven-span successful hierarchy.

Raw evidence is under ignored `validation-artifacts/batch3/`: `success.json`, `postgres-failure.json`, `recovery.json`, `restored.json`, and `services.log`. Jaeger was intentionally restarted and shut down, so these historical IDs are not promised to remain available in its transient UI.

## 12. Observability failure behavior

With Collector stopped, all three application processes restarted, both storage readiness endpoints returned 200, and two identical checkout attempts returned 200. SDK logs recorded `traces export: processor export timeout: rpc error: code = DeadlineExceeded desc = context deadline exceeded`. These failures occurred on the background export path; no business request depended on successful telemetry delivery.

With Jaeger stopped, readiness stayed at 200 and a new checkout returned 200. Collector logged a connection refusal to Jaeger's OTLP port and retried. After restoration, a fresh complete distributed trace appeared. Buffers and retries are bounded and not durable: temporary trace loss remains expected during outages.

## 13. Existing behavior regression check

`.\scripts\validate-stateful.ps1 -Project aegis-batch3-validation` finished with exit code 0 and `All stateful validation checks passed.` Real PostgreSQL tests passed (payment package 0.025s, `TestPostgresLedger` 0.02s), and real Redis tests passed (inventory package 0.083s, `TestRedisReservations` 0.05s). These include competing and duplicate requests against actual datastores, not just handler stubs.

The end-to-end order `validation-11a697be2dfb43b1a42a3b049c7dae2a` returned 200 twice. `sku-001` decreased from 94 to 92 once, stayed at 92 after replay and both conflicts, and still held 92 after application/datastore restarts. PostgreSQL inspection returned exactly `1:2500` (one row, original amount). Quantity and amount conflicts and insufficient stock returned 409.

During PostgreSQL outage, Payment liveness returned 200, readiness/charge returned 503, and Checkout returned 502 after retaining a reservation. `sku-002` went from 50 to 49 and stayed at 49 when the same order completed after recovery; the recovered ledger returned `1:1000`. During Redis outage, Inventory liveness returned 200 while readiness/reserve returned 503. Recovery and datastore/application restart checks passed. Logs included clean service-stopped messages during restart. The script finally removed its containers/network and preserved both named datastore volumes.

## 14. Resource impact

Collector adds a 128 MiB limit and Jaeger adds 256 MiB, both at 0.5 CPU. Combined additions: 384 MiB; total configured runtime memory: 1,152 MiB. A light-traffic `docker stats --no-stream` sample after outage/recovery showed Collector 16.49 MiB / 128 MiB and Jaeger 10.62 MiB / 256 MiB. These are observed snapshots, not load-test guarantees. The inherited stateful test container uses a 1 GiB hard limit, 512 MiB Go soft limit, and serial package compilation.

## 15. Known limitations and scope choices

- Jaeger memory storage is transient and limited to 2,000 traces. SDK/Collector queues can drop data. Shutdown flushing is bounded and best effort.
- Development root traces are always sampled, while an incoming unsampled parent is respected. This is not a production sampling policy.
- High-level datastore spans omit individual commands/SQL. Credentials and arbitrary payloads are excluded; expected business outcomes are not marked as datastore infrastructure failures.
- Standard HTTP CLIENT semantics may label downstream 4xx responses as client errors; inspect the business outcome/status before interpreting them as infrastructure faults.
- The validator uses the pinned Jaeger UI JSON API. It is not a durable Aegis backend integration contract.
- Collector and Jaeger availability is explicitly outside readiness and startup dependencies. Missing local export configuration disables export rather than making tests require a backend.
- The optional Redis-outage trace inspection was not added: PostgreSQL proved the required failure hierarchy, while Batch 2 regression covers Redis outage business behavior.
- Metrics, OTel log export, durable trace storage, Aegis analysis/control plane, AI, ChaosBench, messaging, cloud infrastructure, retries of business operations, and compensation remain unimplemented.
- The intentional reservation-before-payment consistency limitation remains unchanged.

## 16. Recommended next step and review order

Next batch: define a small read-only trace ingestion and service-dependency reconstruction component for Aegis, with explicit contracts and tests using this trace topology. Do not expand into diagnosis/ML until the evidence model is clear. This recommendation was not implemented.

Review: ADR 0007 and Collector/Jaeger YAML; shared telemetry initialization and HTTP lifecycle; HTTP transport and storage spans; tracing tests and saved JSON; validation scripts; tracing guide and README.

## 17. Final Git status

All implementation work remains in the working tree for human review. `git diff --check` passed. The final `docker compose -p aegis-batch3-validation ps -a` was empty. Both `aegis-batch3-validation_postgres-data` and `aegis-batch3-validation_redis-data` remain. No persistent volumes were deleted.

The following status includes the pre-existing user edit to `prompt.md`. The diff stat includes tracked files only; the new untracked tracing/configuration/documentation files are listed by status. Git also printed LF-to-CRLF conversion notices under the repository's Windows checkout configuration; these were not whitespace-check failures.

```text
$ git status
On branch feature/shopsim-observability
Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
        modified:   .gitignore
        modified:   README.md
        modified:   docker-compose.yml
        modified:   docs/architecture/architecture-v0.md
        modified:   go.mod
        modified:   go.sum
        modified:   prompt.md
        modified:   scripts/validate-stateful.ps1
        modified:   shopsim/checkout/main.go
        modified:   shopsim/checkout/main_test.go
        modified:   shopsim/internal/httpio/httpio.go
        modified:   shopsim/inventory/main.go
        modified:   shopsim/inventory/main_test.go
        modified:   shopsim/inventory/store.go
        modified:   shopsim/inventory/store_integration_test.go
        modified:   shopsim/inventory/store_test.go
        modified:   shopsim/payment/main.go
        modified:   shopsim/payment/main_test.go
        modified:   shopsim/payment/store.go
        modified:   shopsim/payment/store_integration_test.go
        modified:   shopsim/payment/store_test.go

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        docs/adr/0007-tracing-first-observability.md
        docs/observability/
        docs/reports/
        observability/
        scripts/validate-observability.ps1
        scripts/validation-helpers.ps1
        shopsim/checkout/tracing_test.go
        shopsim/internal/httpio/tracing_test.go
        shopsim/internal/telemetry/

no changes added to commit (use "git add" and/or "git commit -a")

$ git diff --stat
 .gitignore                           |    1 +
 README.md                            |   12 +-
 docker-compose.yml                   |   31 +
 docs/architecture/architecture-v0.md |   24 +-
 go.mod                               |   26 +-
 go.sum                               |   74 +-
 prompt.md                            | 1270 +++++++++++++++++++++-------------
 scripts/validate-stateful.ps1        |   30 +-
 shopsim/checkout/main.go             |   23 +-
 shopsim/internal/httpio/httpio.go    |   52 +-
 shopsim/inventory/main.go            |    5 +-
 shopsim/inventory/store.go           |   28 +-
 shopsim/payment/main.go              |    5 +-
 shopsim/payment/store.go             |   23 +-
 14 files changed, 1051 insertions(+), 553 deletions(-)
```
