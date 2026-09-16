# Batch 5: evidence-backed time-window summaries

Batch 5 describes qualifying **observed telemetry** during an explicit period. It
does not decide whether that behavior is anomalous or identify a root cause.

## Shared architecture

```text
Jaeger / Jaeger export / normalized fixture
                  |
             Trace / Span
                  |
       extract_observations (observations.py)
       merge fragments, deduplicate, quarantine,
       validate parents, services and cycles
                  |
       canonical CrossServiceObservation
                  |
          +-------+------------------+
          |                          |
 reconstruct_dependencies     summarize_evidence(TimeWindow)
          |                          |
   Batch 4 ServiceGraph        Batch 5 EvidenceSummary
          |                          |
 aegis dependencies             aegis evidence
```

Both consumers call the same extractor. There is no second interpretation of a
valid service relationship in the evidence aggregator. The graph's node collection,
edge/error counts, diagnostics and v1 JSON contract remain compatible. All original
Batch 4 tests are retained without changed expectations.

`observations.py` owns the span integrity logic formerly in `graph.py`. It merges
fragments before parent resolution, scopes IDs by trace, collapses identical
duplicates, quarantines conflicts, ignores same-service links, and omits invalid
ancestry. Nodes still include known isolated services for the graph consumer.
Each observation retains source/target identities and its normalized parent/child
spans. `EvidenceReference` exposes trace, caller-span and callee-span IDs explicitly.

A narrow extra check compares duplicate timing **types**, because Python considers
`True == 1 == 1.0`. Normalized sources already reject noninteger timing; direct
library callers can still construct such invalid dataclasses. Conflicting timing
types are quarantined, so arrival order cannot determine whether timing is usable.
This does not change the semantics of valid Batch 4 normalized data.

## Exact evidence semantics

| Concept | Contract |
| --- | --- |
| One observation | One valid direct cross-service parent-child relationship, counted once after deduplication |
| Identity/provenance | Exact `(trace_id, caller_span_id, callee_span_id)`; repeated distinct calls in one trace retain distinct references |
| Window | `TimeWindow(start_us, end_us)`, integer epoch microseconds, `start_us < end_us` |
| Membership | Child `start_time_us` satisfies `start_us <= timestamp < end_us` |
| Missing/invalid timestamp | Exclude the otherwise valid observation from windowed counts and report `unclassifiable_observation_time` |
| Explicit error | Child status is exactly `error`; parent, ancestor and sibling errors do not propagate |
| Error rate | `error_count / observation_count`, a numeric fraction using all qualifying observations |
| Edge latency | Full parent duration, only for parent `client` and child `server` kinds |
| Missing/inapplicable latency | Keep observation, explicit error and provenance; omit only the latency sample |
| Average | Arithmetic mean of valid caller durations; finite integer or float in microseconds |
| Percentiles | Nearest rank: sort, `ceil(p*N)`, select the 1-based sample; p50/p95 remain integer observed microseconds |
| No latency samples | `sample_count: 0`, average/p50/p95 all JSON null |
| Completeness | Always observed telemetry only; sampling and query completeness unknown |

An `unset` status is not an explicit error and is not proof of success. Error rate
is the fraction carrying explicit error evidence, not an inferred fraction of all
real failures. Zero-count edges are omitted; no division by zero is performed.

Windowing occurs **after** canonical extraction, so parents outside the window can
support children inside it. Child start determines membership even if the parent
starts earlier, the child ends later, or the duration spans another window. No
duration is clipped to the window and no SERVER processing duration is substituted
or counted as a second sample. Non-CLIENT/SERVER relationships can still establish
dependency observations, but not this particular caller-latency measurement.

As in the existing sources, valid span timing is a nonnegative integer; boolean,
float, null, negative and non-finite values are not usable timing evidence. Zero
duration is valid. Window boundaries may precede epoch zero, but negative span
timestamps remain invalid under the existing normalized source convention.

The standard-library arithmetic mean avoids arrival-order-dependent floating
accumulation over integer inputs. Extremely large artificial durations whose
nonintegral mean cannot fit the numeric output range fail explicitly with a
ValueError; the implementation never writes NaN or Infinity. Real telemetry values
remain in the existing integer microsecond convention without unit conversion.

## JSON contract

`EvidenceSummary` has its own v1 JSON contract, separate from the unchanged graph
v1 contract. Example from the normalized `timed-calls.json` fixture:

```json
{
  "schema_version": 1,
  "window": {"start_us": 1000, "end_us": 2000},
  "edges": [{
    "source": "gateway",
    "target": "worker",
    "observation_count": 3,
    "error_count": 1,
    "error_rate": 0.3333333333333333,
    "latency_us": {"sample_count": 2, "average": 200, "p50": 100, "p95": 300},
    "provenance": [
      {"trace_id": "t1", "caller_span_id": "a", "callee_span_id": "b"},
      {"trace_id": "t1", "caller_span_id": "c", "callee_span_id": "d"},
      {"trace_id": "t2", "caller_span_id": "e", "callee_span_id": "f"}
    ]
  }],
  "limitations": ["observed_telemetry_only", "sampling_and_query_completeness_unknown"],
  "diagnostics": [{"code": "unavailable_caller_duration", "trace_id": "t2", "span_id": "e"}]
}
```

Edges sort by source/target; provenance sorts by trace/caller/callee IDs, without
truncation. Diagnostics are deduplicated and sorted by code/trace/span. Limitations
have a fixed order. Serialization sorts object keys, indents by two spaces, and
ends with one newline. Input trace order, span order and duplicate deliveries do
not affect output bytes for the same logical evidence. There are no generated
execution timestamps or random IDs in analytical output.

Provenance supports lookup of every contributing operation, including observations
whose latency was unavailable. There is no persistent evidence database: keep the
normalized export when provenance needs to remain resolvable after Jaeger expires
its in-memory traces. The live validator saves both trace evidence and summaries.

## Diagnostics and limitations

Normalization and canonical integrity diagnostics are preserved for the **entire
supplied batch**, even if affected spans lie outside the chosen window. They do not
claim to measure window-local data loss. New diagnostics are limited to:

| Code | Attached span | Meaning |
| --- | --- | --- |
| `unclassifiable_observation_time` | Child | A valid relationship lacks a usable membership timestamp |
| `inapplicable_edge_latency` | Child | An in-window observation is not CLIENT-to-SERVER |
| `unavailable_caller_duration` | Parent | An in-window CLIENT-to-SERVER observation lacks valid caller duration |

Unclassifiable timestamps are reported because their window cannot be known.
Latency diagnostics are emitted only for qualifying in-window observations; an
out-of-window call's latency is not needed for the requested summary.

The source query and analysis window are separate: `--lookback` and `--limit` bound
what Jaeger supplies, while `--start-us`/`--end-us` filter its returned observations.
A past window does not automatically expand Jaeger's lookback or bypass its limit.
Sampling, telemetry loss, incomplete spans and query limits can all hide calls.
There is no reliable global sampling percentage in the normalized model, so none
is invented. An empty result means no qualifying observations were available in
this evidence/window; it does not mean no calls occurred.

## Boundaries and learning landmarks

This is pure batch analysis: source-independent dataclasses, a shared canonical
observation definition, and a deterministic aggregator. Python's standard library
is sufficient; no statistics framework or runtime dependency was added. The CLI
reuses the same source arguments and source factory for both commands.

Caller duration measures experienced request latency, including everything timed
by that CLIENT span; it is not a decomposition of network versus server processing
time. Client-only failures with no child span cannot establish a dependency here.
Same-service network calls, asynchronous links, database-instance edges and missing
calls remain outside this model. No baseline, threshold, anomaly decision, causal
inference, root-cause ranking, AI interpretation or remediation is implemented.

Review `extract_observations`, `CrossServiceObservation`, `EvidenceReference`,
`TimeWindow`, `summarize_evidence`, `_latency_summary`, and `cli.main` to follow the
flow. The fixture and `test_evidence.py` demonstrate boundaries and missing evidence;
`test_evidence_cli.py` covers input selection and output behavior.
