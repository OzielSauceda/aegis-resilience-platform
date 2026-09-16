# Batch 5 completion report

## Files changed

Starting branch: `feature/aegis-evidence-summaries`. Starting commit: `da21d7e`,
the Batch 4 merge, also the local `main`/`origin/main` tip at inspection. Only the
user's `prompt.md` was modified initially. That file remains untouched by this work.

| Path | Purpose and important changes |
| --- | --- |
| `analysis/src/aegis_analysis/observations.py` (new) | Moves Batch 4 integrity/relationship logic into the shared canonical extractor; adds observation and exact provenance models |
| `analysis/src/aegis_analysis/evidence.py` (new) | TimeWindow, EdgeEvidence, LatencySummary and EvidenceSummary models; deterministic windowed counts, errors, latency, provenance and diagnostics |
| `analysis/src/aegis_analysis/graph.py` | Aggregates the shared observations into the unchanged Batch 4 graph contract |
| `analysis/src/aegis_analysis/cli.py` | Adds `evidence`, explicit window arguments, human/JSON output and shared source arguments/factory |
| `analysis/src/aegis_analysis/__init__.py` | Exports `TimeWindow` and `summarize_evidence` alongside existing library entry points |
| `analysis/tests/test_evidence.py` (new) | Domain semantics, integrity, statistics, provenance and deterministic ordering checks |
| `analysis/tests/test_evidence_cli.py` (new) | Window errors, source selection, output streams, fixtures and CLI/library equality checks |
| `analysis/tests/fixtures/timed-calls.json` (new) | Two traces with repeated same-edge calls, differing client/server durations, a missing caller duration and an end-boundary exclusion |
| `scripts/validate-evidence.py` (new) | Real existing-stack validation with two successes, PostgreSQL outage, actual provenance/statistics verification and live/offline CLI comparisons |
| `analysis/README.md` | Evidence usage, module map, examples, exit/stream behavior and live validation commands |
| `docs/architecture/batch-5-evidence.md` (new) | Full evidence contract, diagnostic scope, learning notes and limitations |
| `docs/architecture/architecture-v0.md` | Shows the shared observation layer and both implemented analytical outputs |
| `README.md` | Announces Batch 5 and links current setup/contract/report |
| `docs/reports/batch-5.md` (new) | This completion and validation record |

Existing Batch 4 tests and fixtures were not modified. ShopSim, Go dependencies,
Compose, Jaeger adapters, normalized source formats and existing validation scripts
were not modified. No runtime dependencies or permanent containers were added.

## Architecture implemented

```text
Jaeger / Jaeger file / normalized fixture
                    |
                Trace / Span
                    |
           extract_observations
           merge + deduplicate + quarantine
           validate services + parents + cycles
                    |
         CrossServiceObservation + provenance
                    |
           +--------+---------------------+
           |                              |
 reconstruct_dependencies         summarize_evidence(TimeWindow)
           |                              |
  Batch 4 ServiceGraph             per-edge EdgeEvidence
           |                              |
 aegis dependencies               aegis evidence
```

Graph reconstruction and evidence aggregation share **the same observation
extraction function**. The evidence layer does not rediscover parent relationships.
It only classifies canonical observations into a window and aggregates their
counts, explicit errors and applicable caller durations.

## Exact semantics

- One valid direct cross-service parent-child relationship is one observation.
  CLIENT and SERVER spans do not each add a separate count. Repeated distinct calls
  count separately; duplicate deliveries do not.
- Window membership uses the callee/child `start_time_us`. The interval is
  `[start_us, end_us)` with integer boundaries and `start_us < end_us`. Extraction
  happens before filtering, preserving parents outside the selected window.
- Missing/invalid child timestamps cannot enter a window. A deterministic diagnostic
  explains their omission; no parent timestamp or operation-name fallback is used.
- An error is exactly child `status == "error"`. Error-rate denominator is all
  qualifying observations; unset is neither an explicit error nor proof of success.
- Latency is the full caller/parent duration only for CLIENT-to-SERVER relationships.
  It is not clipped to the window, mixed with SERVER processing time or doubled.
- Missing or inapplicable latency preserves observation/error counts and provenance.
  Only the latency sample is omitted; sample count exposes the difference.
- Valid timing follows the existing nonnegative integer microsecond convention.
  Zero duration is valid. Boolean/float/non-finite/negative values are unusable.
- Average is arithmetic mean. Percentiles use sorted nearest rank with exact
  integer rank arithmetic: `ceil(p*N)`, then select the 1-based sample. P50/p95
  remain actual integer duration samples. Empty latency statistics are null.
- Every edge retains every `(trace_id, caller_span_id, callee_span_id)` reference,
  sorted deterministically without truncation.
- Output always declares `observed_telemetry_only` and
  `sampling_and_query_completeness_unknown`. No sampling rate is invented. Empty
  evidence states only that no qualifying observations were available.
- Normalization/canonical diagnostics cover the entire supplied batch. New latency
  diagnostics concern only in-window observations; unclassifiable timestamps cannot
  be assigned a window. JSON sorts edges, provenance, diagnostics and object keys,
  disallows NaN/Infinity and ends with a newline.

See the [full contract](../architecture/batch-5-evidence.md) for the schema and
diagnostic codes. The existing dependency graph's v1 schema remains unchanged.

## Tests

Commands were run from the repository root using the existing MSYS2 Python virtual
environment. Standard Windows Python uses `Scripts/python` instead of `bin/python`.

| Command | Passed | Failed | Result |
| --- | --- | --- | --- |
| `analysis/.venv/bin/python -m pytest analysis -q` (before implementation) | 43 | 0 | Batch 4 baseline |
| `analysis/.venv/bin/python -m pytest analysis -q` (after implementation) | 108 | 0 | 43 original + 65 new tests; 0.27s |
| `go test ./...` | 5 packages | 0 | All packages passed, cached; this command does not emit individual test counts |
| `docker compose -p aegis-batch5-validation build` | 3 application images | 0 | Existing images built successfully |
| `analysis/.venv/bin/python scripts/validate-evidence.py` | All live assertions | 0 | Exit 0; exact three-trace validation passed |
| `./scripts/validate-stateful.ps1 -Project aegis-batch5-validation` | All script checks; 2 real-store test packages | 0 | Exit 0; all stateful validation checks passed |
| `./scripts/validate-observability.ps1 -Project aegis-batch5-validation` | All script checks | 0 | Exit 0; all observability validation checks passed |
| `analysis/.venv/bin/python scripts/validate-trace-analysis.py --project aegis-batch5-validation` | All Batch 4 live assertions | 0 | Exit 0; graph success/failure, live/offline equality and recovery passed |
| `analysis/.venv/bin/aegis evidence --fixture analysis/tests/fixtures/timed-calls.json --start-us 1000 --end-us 2000` | Console entry point | 0 | Exit 0; 3 observations, 1 error, 2 samples; expected provenance and warnings |
| `git diff --check` | Whitespace check | 0 | Exit 0; only Git LF/CRLF conversion notices |

The new tests cover all requested Batch 5 semantics, including repeated calls in
one trace, duplicate fragments, conflicts, cycles, unknown services, trace-local
IDs, missing timing, ancestor/sibling error isolation, separate latency sample
counts, arithmetic mean, nearest-rank distributions and single/zero samples,
complete provenance, deterministic shuffled output, invalid CLI windows and all
source selectors. Existing tests continue to check graph and CLI compatibility.

Docker was initially inaccessible inside the sandbox; the authorized Docker retry
succeeded. No source or test expectation was changed to address that environment
restriction.

The stateful script ran `go test -p 1 -v -count=1 ./shopsim/payment ./shopsim/inventory`
in its existing test container. Payment passed in 0.031s, Inventory in 0.060s,
including `TestPostgresLedger` and `TestRedisReservations`. End-to-end checks passed
for idempotent replay, amount/quantity conflicts, insufficient stock, both datastore
outages, retained reservation, manual recovery, one recovered payment, and
application/datastore restarts. No assertion totals are invented for scripts that
do not report a framework test count.

The unmodified observability script passed seven-span/shared-trace validation,
Collector receipt, PostgreSQL failure and recovery, retained reservation, one
recovered payment, exclusion of probe spans, application startup/checkout with
Collector down, checkout with Jaeger down, and restored trace delivery. Its restored
trace was `5da88d81d3c440f5f9410a7137f85b0d`. It finished with
`All observability validation checks passed.` and exit 0.

The original Batch 4 live validator also passed unchanged after the shared
extractor refactor. Success trace `637e62978e47d81c8bfaffc0d6880ddb` and failure
trace `179ca52024e6835f165a983c88b91c7f` each reconstructed both expected edges.
Only the Payment edge carried an error in the failure trace. Original live/fixture
JSON equality, retained reservation and recovery-without-double-reservation checks
passed. It finished with `All Batch 4 live validation checks passed.` and exit 0.

## Real validation

The validator used the existing Compose file under the isolated project
`aegis-batch5-validation`. It generated exactly these orders and retrieved one
complete seven-span trace for each:

| Outcome | Order suffix (common prefix `batch5-f90473c8c6d6460c98223741ed493625`) | Trace ID |
| --- | --- | --- |
| HTTP 200 | `-success-1` | `9455375615c66fba2db9fbfa2aed9c82` |
| HTTP 200 | `-success-2` | `1072df79409b064326d40264ab34d63f` |
| HTTP 502 after PostgreSQL stop | `-failure` | `b718e5c10716275d175060d86a21c6fc` |

The containing window derived from actual returned span clocks was
`[1789411835853368, 1789411838453210)` microseconds. A service query with limit 100
returned exactly those three trace IDs, with no unrelated traces. Results:

| Edge | Observations | Errors | Error rate | Latency samples | Average us | P50 us | P95 us |
| --- | --- | --- | --- | --- | --- | --- | --- |
| checkout -> inventory | 3 | 0 | 0.0 | 3 | 1934.6666666666667 | 1815 | 2521 |
| checkout -> payment | 3 | 1 | 0.3333333333333333 | 3 | 336133.6666666667 | 4639 | 1002159 |

Actual sorted caller durations were `[1468, 1815, 2521]` for Inventory and
`[1603, 4639, 1002159]` for Payment. The validator recalculated counts, errors,
averages and percentile ranks from the returned spans and checked equality.

Exact provenance was verified against each span's service owner, kind and parent:

| Trace ID | Edge target | Caller span | Callee span |
| --- | --- | --- | --- |
| `1072df79409b064326d40264ab34d63f` | inventory | `da28f3163b2638f0` | `f2eca893e61f165c` |
| `9455375615c66fba2db9fbfa2aed9c82` | inventory | `2796ab093ca78fc8` | `bc0ded08add2a92d` |
| `b718e5c10716275d175060d86a21c6fc` | inventory | `9916df5c329132f7` | `f6d81f87a7fa88c4` |
| `1072df79409b064326d40264ab34d63f` | payment | `91a4d950f9310bf1` | `f548dcdef8186279` |
| `9455375615c66fba2db9fbfa2aed9c82` | payment | `5ce07e620940997d` | `a0d8aa53dd7b017c` |
| `b718e5c10716275d175060d86a21c6fc` | payment | `6d48bde126e795f7` | `1929a569657d3c19` |

There were no diagnostics. Both completeness limitations remained present. Live
service-query JSON, per-trace CLI JSON and normalized-fixture replay matched their
library summaries byte for byte. The failed checkout retained its stock
reservation. PostgreSQL was restarted in a finally block, then project containers
were stopped; volumes and orders were preserved.

Local artifacts: `validation-artifacts/batch5/normalized.json`, `evidence.json` and
`validation.json`. These are ignored by Git; the exact results above remain in this
reviewable report even when transient Jaeger storage is gone.

## Important engineering decisions

The suggested architecture is implemented directly as `observations.py` and
`evidence.py`. The existing Span/Trace/source models and dependency graph schema
did not need revision. One shared CLI source factory prevents duplicated source
selection logic. No new dependency, database, API or long-running service is needed.

There is one narrow integrity tightening for direct library input: conflicting
timing types are quarantined even when Python equality says their values match
(`True`, `1`, `1.0`). Valid normalized data and original Batch 4 tests retain their
semantics. Without this check, malformed duplicate timing could make window or
latency qualification depend on which record arrived first.

Windowing is a local analytical filter, not a Jaeger query-completeness guarantee.
Lookback and query limits remain explicit source options. Diagnostics retain their
existing batch-wide scope; only latency qualification warnings are window-local.
Human evidence mode sends warnings/limitations to stderr; JSON embeds them in the
document. Existing `dependencies` presentation/exit behavior is preserved.

## Remaining limitations

The model cannot determine exact sampling rate, unseen calls, client-only failures
without a child span, global traffic completeness, true network-latency decomposition,
asynchronous links, same-service network dependencies, database-instance topology,
anomaly status or root cause. It retains provenance but not durable telemetry:
exports are needed to resolve IDs after Jaeger discards traces. Percentiles are
descriptive observations even when the sample count is small. Deduplication remains
within one invocation; there is no persistent ingestion state or streaming window.

## Suggested human review landmarks

1. `observations.py:extract_observations` — the single integrity/relationship definition.
2. `CrossServiceObservation` and `EvidenceReference` — observation identity and child error semantics.
3. `evidence.py:TimeWindow` — explicit validated `[start,end)` contract.
4. `evidence.py:summarize_evidence` — window classification and preservation of non-latency evidence.
5. `evidence.py:_latency_summary` — integer nearest ranks and arithmetic mean.
6. `EvidenceSummary.to_dict/to_json` — deterministic provenance, limitations and output contract.
7. `cli.py:main` — shared sources, explicit window arguments and existing command compatibility.
8. `test_evidence.py` and `fixtures/timed-calls.json` — concrete semantics with incomplete and repeated calls.

## Git status

Final `git status --short`:

```text
 M README.md
 M analysis/README.md
 M analysis/src/aegis_analysis/__init__.py
 M analysis/src/aegis_analysis/cli.py
 M analysis/src/aegis_analysis/graph.py
 M docs/architecture/architecture-v0.md
 M prompt.md
?? analysis/src/aegis_analysis/evidence.py
?? analysis/src/aegis_analysis/observations.py
?? analysis/tests/fixtures/timed-calls.json
?? analysis/tests/test_evidence.py
?? analysis/tests/test_evidence_cli.py
?? docs/architecture/batch-5-evidence.md
?? docs/reports/batch-5.md
?? scripts/validate-evidence.py
```

Batch 5 modifies six tracked files and adds eight new files. The additional
`prompt.md` modification is pre-existing user input, untouched by the agent.
Nothing is staged or committed. The branch remains
`feature/aegis-evidence-summaries` at `da21d7e`. No commit, push, merge, rebase,
destructive Git command or pull request has been performed. Validation containers
were stopped by the scripts; named volumes and local evidence exports were preserved.
