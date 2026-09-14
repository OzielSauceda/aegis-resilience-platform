# 0007 — Start ShopSim observability with OpenTelemetry traces

## Status

Accepted

## Context

ShopSim already has real HTTP and datastore boundaries. Aegis needs causal evidence of successful and failed requests before analysis components are introduced. The 16 GB local development environment calls for a small observability stack with a clear operational boundary.

## Decision

Instrument three Go services with a shared internal tracing initializer, standard otelhttp server/client wrappers, W3C Trace Context and Baggage propagation, and one manual child span per high-level Redis/PostgreSQL operation. Use parent-based always sampling in local development, bounded nonblocking SDK batching, and bounded graceful shutdown flushing.

Export traces via OTLP/gRPC to the official core Collector 0.160.0, then via OTLP/gRPC to pinned Jaeger 2.20.0 with transient memory storage. Core contains every component needed; contrib would add no value to this pipeline. Collector and Jaeger receive 128 and 256 MiB memory limits respectively. Exclude probes and defer metrics and OpenTelemetry log exporting.

Neither telemetry container participates in ShopSim startup dependencies or readiness checks. Telemetry configuration/export problems cannot become business availability requirements. Error spans use sanitized evidence; expected business outcomes retain their HTTP semantics and datastore outcome attributes.

## Consequences

Requests become connected traces that expose service and datastore latency, failure location, and propagation. Vendor-neutral instrumentation can later feed other analysis/storage backends without altering business code. A shared package is appropriate within the existing one-module layout; no new module or framework is needed.

Asynchronous delivery can lose spans during outages, queue pressure or forced shutdown. Jaeger restarts discard traces. Always sampling and a local unprotected UI are development choices. Operation-level datastore spans omit per-command SQL/Redis details. The tracing API adds dependencies (including a no-op metric API required by HTTP instrumentation), but there is no metric SDK pipeline or log exporter. Batch 2 idempotency, sequential orchestration, and deliberate partial failure remain unchanged.
