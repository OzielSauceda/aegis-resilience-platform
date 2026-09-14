# Batch 4: trace analysis and dependency reconstruction

## Implemented boundary

```text
ShopSim Go services (system under observation)
    | OpenTelemetry SDKs, OTLP/gRPC
    v
OpenTelemetry Collector
    | OTLP/gRPC
    v
Jaeger v2 (existing transient trace storage)
    | HTTP query JSON
    v
JaegerSource / normalize_jaeger       FixtureSource
    |                                   |
    +----------- Trace / Span -----------+
                        |
              reconstruct_dependencies
                        |
                  ServiceGraph
                        |
              human text / v1 JSON
```

The top-level `analysis/` directory belongs to Aegis, alongside `shopsim/`,
`observability/`, `scripts/`, and `docs/`. Its `src/aegis_analysis` package uses
standard Python packaging, with no application framework or runtime dependencies.
The controlled business application remains entirely Go. Telemetry is downstream
of business operations; running or crashing the analyzer cannot block checkout.

## Domain model and source adapter

A domain model describes what Aegis needs to reason about. A normalized span is
one recorded operation, owned by a service, within a distributed trace. It is not
a service, an entire request, or evidence that a database is a separate service.

| Span field | Meaning and purpose |
| --- | --- |
| `trace_id` | Groups causally related operations and scopes span identity |
| `span_id` | Identifies this operation within its trace; used for deduplication |
| `parent_span_id` | Direct parent in the same trace, or null; supplies relationship evidence |
| `service_name` | Owning service identity, or null if missing; supplies graph endpoints |
| `operation_name` | Display/context only; never parsed to infer topology |
| `span_kind` | Client/server/internal/producer/consumer/unknown context; retained without requiring HTTP-only traces |
| `start_time_us` | Integer microseconds since Unix epoch, or null if unavailable |
| `duration_us` | Nonnegative integer microseconds, or null; retained for future temporal analysis |
| `status` | `unset`, `ok`, or `error`; unset is not proof of success |
| `attributes` | Scalar JSON operation evidence retained separately from graph logic |

`Trace` groups spans by trace ID and carries normalization diagnostics. Types are
lightweight dataclasses. Source implementations return tuples of these objects
through the `TraceSource.read_traces()` protocol. The graph algorithm knows
nothing about HTTP, files, process tables, tag arrays, or Jaeger field names.

Normalization converts a vendor representation into this domain model. The
Jaeger adapter resolves `processID` through `processes[processID].serviceName`,
converts `tags` to attributes, maps `span.kind`, and reads direct `CHILD_OF`
references. Times already use microseconds and are preserved as integers.
`error=true` or `otel.status_code=ERROR`/2 means error; explicit OK/1 means ok;
otherwise status remains unset. HTTP codes and operation strings are not a second
status heuristic. Missing timing does not invalidate a parent relationship.

The adapter uses the same `/api/traces?service=...&tags=...&limit=...&lookback=...`
query contract proven by `validate-observability.ps1`. It also supports
`/api/traces/{trace_id}`. Requests have finite timeouts; malformed envelopes,
query errors, connection errors and invalid JSON fail explicitly. A malformed
individual span is omitted with a diagnostic where its containing trace remains
identifiable. Unsupported nested/non-finite attribute values and conflicting
duplicate tag keys are omitted with `invalid_tags`; no tag-order winner is chosen.
Unsupported references, foreign-trace parents, and multiple distinct
parents never produce guessed edges. Repeated identical parent references are
unambiguous. Batch 3 exported files can be analyzed without Jaeger.

This adapter provides decoupling: replacing Jaeger later requires a new source
that returns `Trace` objects, rather than a rewrite of graph reconstruction.

## Reconstruction and counting

1. Merge spans from all supplied trace fragments into an index keyed by
   `(trace_id, span_id)`. The input may arrive in any order.
2. Collapse identical span records. If the same identity has conflicting content,
   quarantine all copies. No first-arrival or last-arrival winner is chosen.
3. Detect cyclic parent links iteratively and omit relationships touching cycles.
4. Collect known service identities as nodes, including isolated services.
5. Resolve each span's direct parent in the same trace. If evidence is missing,
   report it and omit that relationship; never bridge to a presumed ancestor.
6. If parent and child have known, different owners, observe `parent service ->
   child service`. Same-service spans supply ancestry but create no self-edge.
7. Aggregate each distinct cross-service child span once per input batch; increment
   its edge's error count only if that child span has normalized error status.
8. Sort nodes, directed edges, and diagnostics for deterministic serialization.

A directed edge represents observed parent-to-child service interaction. In the
HTTP hierarchy `checkout SERVER -> checkout CLIENT -> inventory SERVER`, only the
CLIENT-to-SERVER pair crosses service identity and contributes one observation.
The algorithm is independent of those names; tests also use unrelated identities.
It does not require client/server kinds because generic cross-service parent links
are still observations. This means `request_count` is a count of observed span
relationships, not a universal business-request or wire-attempt metric.

An error in the checkout root could come from either branch. Counting the child
status associates failure with the observed callee operation and prevents an
unrelated ancestor failure from contaminating healthy edges. A client-only error
without a downstream span cannot establish an observed callee edge. No descendant
error propagation or root-cause conclusion is implemented.

Indexing and traversal are linear in input span records; sorting costs grow with
the number of nodes, edges and diagnostics. Storage is proportional to the supplied
batch. Repeated exports are deduplicated within a call only, not across separate
executions; the library owns no persistent ingestion state.

## JSON data contract, version 1

```json
{
  "schema_version": 1,
  "nodes": [{"service": "caller"}, {"service": "worker"}],
  "edges": [{"source": "caller", "target": "worker", "request_count": 1, "error_count": 0}],
  "diagnostics": []
}
```

Nodes sort by service; edges sort by source then target; diagnostics sort by code,
trace ID and span ID and are unique. Every diagnostic has `code`, `trace_id`, and
`span_id` (empty when no usable ID exists). Serialization sorts object keys, uses
two-space indentation, and ends with a newline. There are no execution timestamps
or random fields. Identical evidence yields byte-identical JSON independent of
span/trace input order. Error counts are bounded by observation counts.

| Diagnostic | Interpretation |
| --- | --- |
| `invalid_identity` | Missing identity or a span assigned to the wrong trace; omitted |
| `conflicting_duplicate` | Same identity, different normalized records; all copies omitted |
| `unknown_service` | Missing/blank owner; no edge using that endpoint |
| `missing_parent` | Parent absent or quarantined; relationship omitted |
| `cyclic_parent` | Invalid ancestry cycle; relationships touching it omitted |
| `ambiguous_parent` | Invalid, foreign, or multiple parents; no parent selected |
| `unsupported_reference` | Non-CHILD_OF reference; not treated as ancestry |
| `invalid_span`, `invalid_tags`, `invalid_timing`, `invalid_operation` | Adapter found malformed data; span or affected field omitted |

A graph with diagnostics is still useful partial evidence, not a completeness
certificate. A graph without diagnostics is also not proof that all requests or
services were captured. Exact duplicates are silently collapsed because they add
no uncertainty. Differences in retained attributes also make duplicates conflict.

Normalized fixtures use `{"schema_version":1,"traces":[{"trace_id":"...",
"spans":[...]}]}`. Each span explicitly supplies trace ID, span ID, nullable parent
ID and nullable service name; other fields use the documented model defaults.
Fixtures are author-controlled input and fail explicitly on invalid structure.
See `analysis/tests/fixtures/branching.json` for a complete seven-span example.

## Design decisions and learning notes

Python suits the future telemetry/statistics/graph analysis work while dataclasses,
collections, JSON, argparse, and urllib keep this batch lightweight. Pytest is the
only direct testing dependency. No data-science framework is necessary to traverse
parent pointers and count observations.

Separation of concerns is concrete here: sources retrieve, the adapter normalizes,
the graph function analyzes, and the CLI presents. A library plus CLI establishes
the analysis contract with short-lived batch analysis: read a finite collection,
compute, output, and exit. A permanent service would add lifecycle and deployment
work without a current caller that requires it. Jaeger already stores the traces,
so a second database is unnecessary. Deterministic output makes evidence review,
regression tests, and future component integration reliable.

The graph is reconstructed from evidence rather than configuration, preparing
Aegis to analyze applications whose topology is not known ahead of time. Later
incident analysis can use these edges as a starting point, with explicit evidence
quality limits. Dependency direction alone does not establish causal fault origin.

## Limits and next batch

Batch 4 does not model same-service network self-calls, asynchronous links,
database instances, operation-level graphs, durations aggregated into metrics,
sampling corrections, traces that never arrived, or cross-run ingestion state.
Identity is whatever the source supplies; renamed services become distinct nodes.
There is no service-discovery configuration or topology inferred from names.

Current datastore spans explicitly include `db.system.name` (`redis` or
`postgresql`) and `db.operation.name`. Those attributes identify database systems
and operations, but not distinct datastore instances. A later external-dependency
adapter could combine explicit server/namespace identity with database attributes
and model external resources separately. Batch 4 never parses `redis.reserve` or
`postgresql.charge` into graph nodes.

No anomaly scoring, root-cause ranking, causal inference, ML, agent investigator,
remediation, API, frontend, new telemetry database, broker, or permanent container
is implemented. The next logical batch is evidence-backed operation/edge summaries
over explicit time windows, retaining provenance and documenting incomplete data.
This would establish trustworthy inputs for later detection and diagnosis.
