# ShopSim tracing (Batch 3)

OpenTelemetry records how one ShopSim request travels between processes and datastores. This batch instruments the application; it does not implement Aegis detection, analysis, or incident investigation. Metrics and OpenTelemetry log exporting are deferred.

```text
Client -> Checkout -> Inventory -> Redis
                 \-> Payment   -> PostgreSQL

checkout / inventory / payment SDKs
             | OTLP/gRPC, asynchronous batches
             v
OpenTelemetry Collector (core 0.160.0)
             | OTLP/gRPC
             v
Jaeger 2.20.0 -> local query API and UI
```

## What a trace shows

A **trace** is the causal history of one request. A **span** is one timed operation within that history. Each span has its own Span ID; all operations belonging to the same trace share its Trace ID. A parent Span ID identifies the operation that caused a child operation. These relationships connect processes even though each runs its own SDK and clock.

A new checkout without an incoming tracing header normally creates seven spans:

```text
POST /checkout                  checkout SERVER
  HTTP POST                     checkout CLIENT (Inventory request)
    POST /reserve               inventory SERVER
      redis.reserve             inventory CLIENT (one high-level transaction)
  HTTP POST                     checkout CLIENT (Payment request)
    POST /charge                payment SERVER
      postgresql.charge         payment CLIENT (insert/idempotency lookup)
```

Inventory runs before Payment. The storage spans cover the complete existing operation, including replay checks and Redis transaction attempts. They are not a separate span for every Redis command or SQL statement. The datastore CLIENT kind represents work performed against an external dependency; there is no Redis/PostgreSQL server-side instrumentation in this batch.

`otelhttp.NewHandler` extracts the W3C `traceparent`/`tracestate` context from incoming HTTP headers and starts the server span. Checkout's `otelhttp.NewTransport` starts a child client span and injects its context into the outgoing headers. The receiving service uses that parent, preserving the Trace ID. Baggage propagation is enabled through the standard propagator, but ShopSim does not create baggage. Request contexts and the existing deadlines are passed through unchanged. The SDK generates IDs; ShopSim does not construct its own.

## Start and inspect

Use Go 1.27.1 and Docker Desktop with Linux containers. From the repository root:

```powershell
docker compose config --quiet
docker compose build
docker compose up -d --wait
$order = 'demo-' + [Guid]::NewGuid().ToString('N')
$body = @{order_id=$order; sku='sku-001'; quantity=1; amount_cents=2500} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8080/checkout -ContentType application/json -Body $body
```

Open **http://localhost:16686**. Choose service **checkout**, search within the last hour, and optionally set tag `shopsim.order_id=<your order ID>`. The other service names are **inventory** and **payment**, all in namespace **shopsim**. Allow several seconds for asynchronous batches to arrive.

Open the trace and expand its spans. Verify the seven-span hierarchy above, three service identities, matching Trace IDs, different Span IDs, correct parent IDs, positive durations, and HTTP 200 responses. Look for `shopsim.outcome=created` or `replayed` on the datastore spans. Repeat the same request and inspect a new trace: it should show replayed storage operations without another stock decrement or ledger row.

The Collector health endpoint is http://localhost:13133/. Its low-noise debug exporter logs summary counts:

```powershell
docker compose logs otel-collector
```

Collector health means its process/pipeline is running, not that Jaeger is available. The official core image includes every receiver/processor/exporter needed here, so contrib is unnecessary. Its shell-free image is not given a misleading shell-based Docker health check; the validation script probes the real health endpoint. Jaeger's query API is also probed by the script.

## Configuration and lifecycle

`shopsim/internal/telemetry.Init` installs one process-wide TracerProvider with service identity, parent-based always sampling, a batch processor, and an OTLP/gRPC exporter. `httpio.Run` calls it for each executable and handles graceful shutdown. Existing handlers use a small shared internal package because all three executables already share one Go module.

Compose sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` and `OTEL_EXPORTER_OTLP_INSECURE=true`. For Go applications running on the host, set the endpoint to `http://localhost:4317`. The trace-specific `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` override is also accepted by the exporter. The exporter is deliberately gRPC, not selected dynamically by a protocol variable.

With no export endpoint, local processes initialize the SDK and propagation without a network exporter. Tests use in-memory exporters or the default no-op provider and require no telemetry backend. `OTEL_SDK_DISABLED=true` explicitly disables tracing. An invalid exporter configuration logs a warning and continues with a no-op provider.

New development root traces are sampled; an incoming unsampled parent remains unsampled. This deterministic parent-based policy does not randomly drop local requests. It is a development setting, not a production volume-control policy.

SDK queues hold at most 512 spans per process, export at most 128 spans per batch, and flush on a one-second timer. Full queues drop spans instead of blocking requests. Export attempts have a two-second timeout. Exporter retries are telemetry-only background behavior, not retries of ShopSim operations.

On SIGTERM/Ctrl+C, the server allows up to five seconds for active requests to finish, then tracing gets an independent three-second shutdown context to flush completed spans. Flushing is best effort. A forced kill, unavailable Collector, expired export deadline, or full queue can lose spans.

## Collector and Jaeger

`observability/otel-collector.yaml` accepts OTLP/gRPC on 4317, applies a memory limiter and batch processor, then exports through `otlp_grpc/jaeger` to `jaeger:4317`. A basic debug exporter supplies sampled summary counts. There are no metrics or logs pipelines. Internal metrics endpoints are disabled in both observability configurations.

`observability/jaeger.yaml` configures Jaeger v2 with only OTLP/gRPC ingestion, memory limiting/batching, in-memory storage (at most 2,000 traces), and its query UI/API. No persistent trace volume or external storage system is added. Restarting Jaeger discards its existing traces.

| Container | Memory limit | CPU limit | Host ports |
| --- | --- | --- | --- |
| Collector | 128 MiB | 0.5 | Loopback 4317 (OTLP), 13133 (health) |
| Jaeger | 256 MiB | 0.5 | Loopback 16686 (UI/API) |

The additions total 384 MiB configured memory; all seven runtime containers total 1,152 MiB. Collector/Jaeger Go soft memory limits are 96/192 MiB respectively. These are limits rather than expected steady usage. The Go build/test containers and Docker Desktop VM require additional RAM. The environment is intended for light local traffic on the 16 GB laptop.

## Failure investigation and independence

To observe the intentional partial failure:

```powershell
docker compose stop postgres
$order = 'failure-' + [Guid]::NewGuid().ToString('N')
$body = @{order_id=$order; sku='sku-001'; quantity=1; amount_cents=2500} | ConvertTo-Json
# Expect HTTP 502; PowerShell reports that HTTP error.
Invoke-RestMethod -Method Post -Uri http://localhost:8080/checkout -ContentType application/json -Body $body
docker compose start postgres
# Once Payment /readyz returns 200, manually replay the exact request.
Invoke-RestMethod http://localhost:8081/readyz
Invoke-RestMethod -Method Post -Uri http://localhost:8080/checkout -ContentType application/json -Body $body
```

Search Jaeger by the order ID. The failed trace has successful Inventory/Redis work, ERROR on `postgresql.charge`, HTTP 503 on Payment and its HTTP client span, and HTTP 502 on Checkout. The root has a reservation-retained event. The recovery request is a separate trace with the same business order ID: Inventory replays the reservation and Payment creates one ledger row. No automatic compensation or business retry is added.

Datastore errors record sanitized exception events and an error classification (`unavailable`, `timeout`, or `canceled`), never raw driver messages, SQL text, passwords, connection strings, or request bodies. Expected conflicts/insufficient stock/unknown SKU have an outcome attribute without marking the datastore span ERROR. HTTP client instrumentation follows standard HTTP conventions and may mark a downstream 4xx response as a client error; use the datastore outcome and HTTP status to distinguish this from infrastructure failure.

Stop Collector or Jaeger and submit a new checkout:

```powershell
docker compose stop otel-collector
# Submit a new checkout; application readiness and business behavior still work.
docker compose start otel-collector
docker compose stop jaeger
# Submit another checkout; business behavior still works.
docker compose start jaeger
```

ShopSim has no Compose dependency or readiness check for either telemetry container. Payment readiness still checks PostgreSQL only; Inventory readiness checks Redis only. SDK exporter and Collector logs may report connection failures, timeouts, retries, or dropped data. Bounded buffers can bridge short outages but provide no durable delivery guarantee. Once both return, new requests should again appear in Jaeger.

`/healthz` and `/readyz` bypass HTTP instrumentation entirely. Redis seed and datastore ping operations are also untraced. They do not flood Jaeger with probe spans.

## Repeatable validation

```powershell
go fmt ./...
go vet ./...
go test ./...
go mod tidy
docker compose -p aegis-batch3-validation build
.\scripts\validate-stateful.ps1 -Project aegis-batch3-validation
.\scripts\validate-observability.ps1
```

The stateful script runs all Batch 2 checks and real datastore tests using the instrumented code. The new script queries the pinned Jaeger UI JSON API, waits for complete traces, and automatically checks service names, seven spans, Trace IDs, Span IDs, parent links, kinds, positive durations, HTTP fields, storage failures, recovery state, probe exclusion, and successful operations while Collector/Jaeger are stopped. It also restarts application processes with Collector unavailable to verify startup independence. This API is local test tooling, not a stable integration contract for future Aegis.

Both scripts use unique order IDs and preserve PostgreSQL/Redis volumes. They consume finite test stock. The observability script normally stops its stack in a finally block; `-KeepRunning` leaves it up for manual UI inspection. It writes `success.json`, `postgres-failure.json`, `recovery.json`, `restored.json`, and `services.log` under ignored `validation-artifacts/batch3/`. Saved JSON remains inspectable after in-memory Jaeger shutdown. To view a trace in Jaeger, inspect it before shutdown; restarting Jaeger loses earlier UI traces. The saved implementation report records representative evidence separately.

## Limits and next stages

Batch 3 does not add metrics collection, OTel log export, durable trace storage, production sampling, authentication, cloud deployment, automated diagnosis, dependency reconstruction, or fault injection infrastructure. Manual storage spans expose operation-level latency rather than individual Redis commands or SQL calls. The cross-store consistency limitations from Batch 2 remain.

A logical next batch is a small read-only trace ingestion/dependency reconstruction component for Aegis, with explicit data contracts and tests against these traces. It is not implemented here.

References: [OpenTelemetry HTTP instrumentation](https://pkg.go.dev/go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp), [Collector core distribution](https://github.com/open-telemetry/opentelemetry-collector-releases/tree/v0.160.0/distributions/otelcol), [Jaeger v2 configuration](https://www.jaegertracing.io/docs/2.20/deployment/configuration/).
