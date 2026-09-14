# Batch 4 implementation and validation report

## 1. Git state

Implementation started on `feature/aegis-trace-analysis` at
`fa917f442c2a3fdf504e3c76ba730486e2f14e27` (`fa917f4`), the merge of PR #2 for
ShopSim observability. Initial inspection ran `git status --short`,
`git branch --show-current`, and `git log --oneline --decorate -n 10`.
The only pre-existing modification was the user's `prompt.md`; it was preserved.
Batch 3 source was committed and merged before this implementation began.

No commit, push, merge, rebase, branch creation, or pull request was performed.

## 2. Files created or modified

All paths below are relative to the repository root.

| File | Purpose and architectural position |
| --- | --- |
| `analysis/pyproject.toml` | Lightweight setuptools package, Python 3.12+ requirement, pytest extra and console entry point |
| `analysis/src/aegis_analysis/model.py` | Aegis-owned domain model and versioned deterministic graph output |
| `analysis/src/aegis_analysis/sources.py` | Source protocol, normalized fixture reader and controlled input errors |
| `analysis/src/aegis_analysis/jaeger.py` | HTTP/file adapter isolating Jaeger query and JSON details |
| `analysis/src/aegis_analysis/graph.py` | Parent-resolution algorithm, evidence omissions and edge aggregation |
| `analysis/src/aegis_analysis/cli.py` | Input selection, human/JSON presentation and exit behavior |
| `analysis/src/aegis_analysis/__main__.py` | `python -m aegis_analysis` entry point |
| `analysis/src/aegis_analysis/__init__.py` | Small public library entry point for models and reconstruction |
| `analysis/tests/test_graph.py` | Topology, aggregation, deduplication, imperfect traces and deterministic output tests |
| `analysis/tests/test_sources_cli.py` | Adapter, source failures, query encoding, fixtures and CLI contract tests |
| `analysis/tests/fixtures/branching.json` | Normalized, offline seven-span ShopSim-shaped fixture |
| `analysis/README.md` | Setup, commands, module map and validation usage |
| `scripts/validate-trace-analysis.py` | Explicit real-stack validation, controlled PostgreSQL outage, recovery and evidence export |
| `docs/architecture/batch-4-trace-analysis.md` | Domain contract, algorithm, design decisions, learning notes and limitations |
| `docs/architecture/architecture-v0.md` | Updates implemented architecture while distinguishing future work |
| `README.md` | Announces Batch 4 and links analysis setup, architecture and this report |
| `.gitignore` | Excludes the Python environment, caches and generated packaging output |
| `docs/reports/batch-4.md` | This review and validation record |

No ShopSim Go source, Go dependency, Compose configuration, existing validation
script, or observability configuration changed. Local evidence resides under the
already ignored `validation-artifacts/batch4/`; the virtual environment is ignored.

## 3. Final Batch 4 architecture

```text
ShopSim Go services
    | OpenTelemetry SDKs / OTLP gRPC
    v
OpenTelemetry Collector
    | OTLP gRPC
    v
Jaeger v2
    | query HTTP JSON
    v
Jaeger adapter ---------- normalized fixtures
    |                            |
    +------ Aegis Trace/Span -----+
                    |
           dependency reconstruction
                    |
               service graph
                    |
             CLI text / v1 JSON
```

The analyzer runs independently after telemetry delivery. It adds no ShopSim
runtime dependency and no permanent container.

## 4. Python package structure

`analysis/` is alongside `shopsim/` because Aegis analyzes the application rather
than being part of its business workflow. `src/aegis_analysis/` keeps distributable
library code separate from tests. Models and graph logic are independent of input
transport. Sources produce models; the CLI chooses a source and renders results.
Standard-library functionality covers runtime needs. Pytest is the only direct
test dependency; no project manager or data-science framework was introduced.

## 5. Normalized trace model

`Trace` groups spans by `trace_id` and carries normalization diagnostics. `Span`
records trace identity, span identity, nullable direct parent ID and nullable
owning service name. These fields supply graph evidence. `operation_name` retains
operation context; `span_kind` retains instrumentation role; neither operation
names nor service-name heuristics drive reconstruction. `start_time_us` is epoch
microseconds and `duration_us` is elapsed microseconds; both are nullable integers.
`status` is unset/ok/error. `attributes` holds scalar JSON evidence. These values
prepare later analysis without implementing time-series metrics in this batch.

`Edge` carries source, target, observation count and child-error count.
`ServiceGraph` contains sorted service nodes, directed edges and diagnostics, with
`schema_version: 1`. Its JSON has stable key/list ordering, two-space indentation
and a terminal newline. See the [complete data contract](../architecture/batch-4-trace-analysis.md).

## 6. Jaeger adapter

`JaegerSource` uses the proven Batch 3 service-search endpoint, plus exact trace
lookup. It supports service, lookback, limit, optional tag filters and finite HTTP
timeouts. `normalize_jaeger` resolves process-table service identity, converts tags,
reads same-trace CHILD_OF references, and preserves integer microsecond timing.
It translates explicit error/OTel status into the normalized status enum values.

Malformed query responses fail as source errors. Incomplete identifiable traces
produce diagnostics. Ambiguous or foreign parents are never guessed. The adapter
does not let process IDs, reference dictionaries or Jaeger field names enter graph
logic. `JaegerFileSource` can replay Batch 3 exports; `FixtureSource` reads a
vendor-independent normalized format.

## 7. Graph algorithm

1. Combine trace fragments and index by `(trace_id, span_id)`.
2. Collapse identical duplicates; quarantine conflicting copies.
3. Detect cyclic ancestry, without recursive traversal.
4. Collect known service identities, including isolated nodes.
5. Resolve each direct parent within the same trace.
6. Omit missing, ambiguous, cyclic and unknown-endpoint relationships.
7. For different parent/child owners, increment `caller -> callee` once for that
   distinct child span; increment errors only for an error-status child.
8. Sort and serialize the aggregated graph and evidence-quality diagnostics.

Same-service HTTP and datastore spans remain available to resolve ancestry but
do not create self-edges. There is no name parsing, hard-coded service topology,
ancestor bridging, or storage-operation-to-database-node heuristic.

## 8. Important code to study

Study `Span`/`Trace` in `model.py`, `TraceSource` in `sources.py`,
`normalize_jaeger` in `jaeger.py`, `reconstruct_dependencies` in `graph.py`,
`ServiceGraph.to_dict`/`to_json`, and `cli.main`. The branching fixture and the
failure/duplicate tests demonstrate these boundaries with small inputs.

## 9. System-design decisions

- **Python:** suitable for the forthcoming analytical work, with minimal code and
  no need to replace Go business services.
- **Adapter:** isolates a vendor response contract and retrieval mechanism.
- **Normalized model:** defines the data Aegis needs independently of Jaeger.
- **Library/CLI:** establishes a testable batch contract without service lifecycle,
  API, deployment, or orchestration overhead.
- **No database:** existing Jaeger supplies telemetry; this batch needs no durable
  cursor, cross-run history, or duplicated storage.
- **No ML:** parent links provide explicit relationship evidence; statistical or
  learned inference is outside the task and unnecessary for reconstruction.

The architecture guide explains domain models, normalization, adapters, directed
graphs, reconstruction, separation of concerns, decoupling, batch analysis, data
contracts and determinism using this implementation's concrete examples.

## 10. Tests

Commands were run from the repository root on Python 3.12.7 (MSYS2), with the
virtual-environment interpreter under `analysis/.venv/bin/python`.

| Exact command | Actual result |
| --- | --- |
| `python -m venv analysis/.venv` | Created the local environment |
| `analysis/.venv/bin/python -m pip install -e './analysis[test]'` | Installed aegis-analysis 0.1.0 and pytest 9.1.1 successfully |
| `analysis/.venv/bin/python -m pytest analysis -q` | 43 passed in 0.22s |
| `analysis/.venv/bin/aegis dependencies --fixture analysis/tests/fixtures/branching.json` | Printed checkout, inventory, payment and both expected edges, each 1 observation / 0 errors |
| `go test ./...` | Exit 0; all five Go packages passed (cached) |
| `docker compose -p aegis-batch4-validation build` | All three existing ShopSim application images built successfully |
| `analysis/.venv/bin/python scripts/validate-trace-analysis.py` | Success/failure graph checks, live/offline CLI equality, retained reservation and recovery replay passed |
| `./scripts/validate-stateful.ps1 -Project aegis-batch4-validation` | Exit 0; all stateful validation checks passed |
| `./scripts/validate-observability.ps1 -Project aegis-batch4-validation` | Exit 0; all observability validation checks passed |
| `analysis/.venv/bin/python -m aegis_analysis dependencies --jaeger-file validation-artifacts/batch3/success.json --json` | Exit 0; both edges have 1 observation / 0 errors; no diagnostics |
| `analysis/.venv/bin/python -m aegis_analysis dependencies --jaeger-file validation-artifacts/batch3/postgres-failure.json --json` | Exit 0; both edges have 1 observation; Payment has 1 error, Inventory 0; no diagnostics |
| `git diff --check` | Exit 0; no whitespace errors (Git reports expected LF/CRLF conversion notices) |

Offline tests cover branching topology, same-service spans, datastore-name
non-inference, generic service names, multiple traces, repeated calls, arbitrary
ordering, trace fragments, exact/conflicting duplicates, missing parents, unknown
services, mismatched IDs, cycles, child errors, malformed source input, ambiguous
references, status normalization, URL encoding, HTTP failures/timeouts, BOM
exports, CLI output, empty input and invalid fixtures.

Initial sandbox attempts could not access the Go cache, Docker or package download
network; the authorized retries succeeded. One stateful-script attempt used
PowerShell `*>` redirection, which converted native Docker progress into a terminating
`NativeCommandError`. Running the existing script without redirection passed; its
implementation was not changed. These initial failures were execution-environment
issues, not passing test results.

## 11. Real trace validation

The initial live validation queried success trace
`bc86c8c1790cdac425df313ea4bf4e2e`, containing seven spans. It observed:

| Edge | Parent span ID | Child span ID | Observations | Errors |
| --- | --- | --- | --- | --- |
| checkout -> inventory | `ca16bd2a09407218` | `da1f6dc224b112e2` | 1 | 0 |
| checkout -> payment | `3d311c687203fc62` | `fc1c863d146363e4` | 1 | 0 |

Nodes were exactly checkout, inventory and payment, with no diagnostics. The
assertions live only in the integration validator. Library tests also reconstruct
`alpha -> beta`, demonstrating that the algorithm does not depend on ShopSim names.

## 12. Failure-trace validation

The same run stopped PostgreSQL and generated a checkout returning HTTP 502.
Trace `f6905dfdcd8a7d7dd56a5f8cd3e3bb93` contained seven spans:

| Edge | Parent span ID | Child span ID | Observations | Errors |
| --- | --- | --- | --- | --- |
| checkout -> inventory | `7602dccdf56cf474` | `1516dbd5bd1d0d59` | 1 | 0 |
| checkout -> payment | `7ded8caf79c84304` | `d274dc62835d3087` | 1 | 1 |

Topology remained the same, with no diagnostics. The downstream Payment span was
marked error; the Inventory span was unset, not explicitly OK. The validator also
confirmed that the failed checkout retained its stock reservation. PostgreSQL was
restarted; manual replay succeeded without a second decrement. This is evidence
of a failed dependency observation, not a root-cause diagnosis.

## 13. Regression testing

The stateful script ran its real-store tests inside the existing build-stage test
container using `go test -p 1 -v -count=1 ./shopsim/payment ./shopsim/inventory`.
Both packages passed, including `TestPostgresLedger` and `TestRedisReservations`.
Reported package durations were 0.032s and 0.043s respectively.

End-to-end checks passed for liveness/readiness, first reservation, exact replay,
amount/quantity conflicts, insufficient stock, PostgreSQL and Redis outages,
retained reservation after payment failure, manual recovery, one recovered ledger
row, and application/datastore restart persistence. The script finished with
`All stateful validation checks passed.` and exit 0.

The unmodified Batch 3 observability script passed seven-span hierarchy and shared
trace ID checks, Collector receipt, success/outage/recovery status checks, retained
reservation and one recovered payment, no health/readiness probe spans, process
startup and checkout with Collector down, checkout with Jaeger down, and restored
trace delivery. It finished with `All observability validation checks passed.` and
exit 0. The resulting real exports were also analyzed by the final Batch 4 adapter:
success `9d19e952c1fb6be287515486cffb4937` and PostgreSQL failure
`b41fb10494c590bfdd3eaa6263ee094a`, both with the expected graphs and no diagnostics.
The restored trace ID was `d366c88629f8247fae276f0567c2ecd7`.

The validation project containers were stopped and removed by the scripts;
named volumes and test orders were preserved. No runtime dependency on Python was
introduced into ShopSim, and none of its business code changed.

## 14. Known limitations

Observation counts are sampled span relationships, not total business requests.
Queries are bounded and Jaeger memory storage is transient. Missing spans cannot
be recovered or inferred. Deduplication is within one analysis invocation only.
Same-service network dependencies, asynchronous links, external datastore identity,
and full operation-level topology are not modeled. Child-error counts do not infer
client-only failures or propagation from descendants. Cycles/conflicts are omitted
conservatively. Timing is retained but no percentiles or anomaly metrics are built.
There is no diagnosis, ranking, causal inference, ML, AI investigator or remediation.

## 15. Recommended Batch 5

Add evidence-backed operation/edge summaries over explicit time windows, with
trace provenance and clear completeness limits. Establish useful error/latency
evidence before anomaly detection or root-cause ranking. Do not yet add an ML
framework, permanent service, or another telemetry database without a concrete need.

## 16. Final Git review

`git status` (branch unchanged; nothing staged):

```text
On branch feature/aegis-trace-analysis

Changes not staged for commit:
        modified:   .gitignore
        modified:   README.md
        modified:   docs/architecture/architecture-v0.md
        modified:   prompt.md

Untracked files:
        analysis/
        docs/architecture/batch-4-trace-analysis.md
        docs/reports/batch-4.md
        scripts/validate-trace-analysis.py

no changes added to commit
```

`git diff --stat`:

```text
 .gitignore                           |    6 +
 README.md                            |    9 +-
 docs/architecture/architecture-v0.md |   14 +-
 prompt.md                            | 2438 +++++++++++++++++++++++++++-------
 4 files changed, 1978 insertions(+), 489 deletions(-)
```

The large `prompt.md` diff is pre-existing user input, untouched by the agent.
Standard `git diff --stat` excludes untracked files: the new package, tests,
fixture, two documents and integration script are also part of this review.
Batch 4 adds 15 files and modifies three existing files. Excluding the user's
prompt, the tracked diff is 22 insertions and seven deletions. All work remains
uncommitted for human review.
