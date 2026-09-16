# Aegis trace analysis (Batches 4–5)

An independent Python library and developer CLI for reconstructing **observed
cross-service dependencies** and evidence-backed time-window summaries. Requires Python 3.12 or newer; tested on 3.12.7.
There are no runtime dependencies. Pytest is an optional development dependency.
ShopSim never imports, invokes, or waits for this package.

## Install and run

From the repository root, using standard Windows Python:

```powershell
python -m venv analysis/.venv
analysis/.venv/Scripts/python -m pip install -e './analysis[test]'
analysis/.venv/Scripts/python -m pytest analysis -q
analysis/.venv/Scripts/python -m aegis_analysis dependencies --fixture analysis/tests/fixtures/branching.json
analysis/.venv/Scripts/python -m aegis_analysis dependencies --service checkout --json
analysis/.venv/Scripts/python -m aegis_analysis dependencies --trace-id YOUR_TRACE_ID --json
```

MSYS2 Python (used on the development machine) creates `analysis/.venv/bin/python`
instead of `Scripts/python`. POSIX Python also uses `bin/python`. Substitute that
path in the commands. Activating the environment also makes `aegis dependencies`
available as an installed console command.

Live queries require the existing ShopSim/Collector/Jaeger stack. Defaults are
`--jaeger-url http://127.0.0.1:16686 --lookback 1h --limit 20 --timeout 5`.
A service search returns a bounded set of traces containing that service, not a
complete inventory of the application. `--trace-id` fetches a specific trace.
There is no polling in the regular CLI, pagination, or historical ingestion job.
Allow the existing telemetry exporter time to deliver a new checkout trace.

`--fixture PATH` reads normalized JSON; `--jaeger-file PATH` reads a Jaeger API
envelope or a single trace exported by Batch 3 (including PowerShell UTF-8 BOM).
Exactly one input selector is required. Output defaults to human-readable text;
`--json` prints only deterministic JSON to stdout. Operational failures use stderr
and exit 1; argument errors exit 2. Successful analysis, including partial or empty
input, exits 0. An empty `dependencies` query emits a stderr notice; it proves no topology absence.
Diagnostics remain in JSON so callers can assess evidence quality.

## Windowed evidence (Batch 5)

Use an explicit half-open window of integer epoch microseconds. With MSYS2/POSIX
Python (substitute `Scripts/python` for standard Windows Python):

```powershell
analysis/.venv/bin/python -m aegis_analysis evidence --fixture analysis/tests/fixtures/timed-calls.json --start-us 1000 --end-us 2000 --json
analysis/.venv/bin/python -m aegis_analysis evidence --fixture analysis/tests/fixtures/timed-calls.json --start-us 1000 --end-us 2000
```

The example reports gateway -> worker: 3 observations, 1 explicit error, and 2
latency samples averaging 200 us (p50 100 us; p95 300 us). The other observation's
caller duration is missing. A fourth call starts exactly at 2000 and is excluded.

The `evidence` command supports the same `--service`, `--trace-id`, `--jaeger-file`
and query options as `dependencies`. Supply `--start-us` and `--end-us` for your
actual trace interval. Jaeger lookback/limit still independently constrain the data
available to analysis; the evidence window does not expand that query automatically.

In human mode evidence, full provenance prints to stdout; limitations and diagnostics
print to stderr. JSON mode includes limitations and diagnostics in the document,
without duplicating warnings on stderr. Invalid windows exit 2 before reading the
source. Source/numeric failures exit 1. Empty evidence exits 0 and explicitly
retains unknown-completeness limitations. The `dependencies` command is unchanged.

```python
from aegis_analysis import TimeWindow, summarize_evidence
from aegis_analysis.sources import FixtureSource

traces = FixtureSource("analysis/tests/fixtures/timed-calls.json").read_traces()
summary = summarize_evidence(traces, TimeWindow(1000, 2000))
print(summary.to_json())
```

See the [Batch 5 contract](../docs/architecture/batch-5-evidence.md) and
[completion report](../docs/reports/batch-5.md) for semantics and validation evidence.

## Live and regression validation

Run sequentially because the projects share host ports. The scripts intentionally
generate orders and exercise store outages; named volumes are preserved. Batch 4
uses a separate `aegis-batch4-validation` project and consumes two `sku-001` units
per successful run. It stops its containers in a finally block. Do not run it
against an unrelated active project using these host ports.

```powershell
docker compose -p aegis-batch4-validation build
analysis/.venv/bin/python scripts/validate-trace-analysis.py
go test ./...
./scripts/validate-stateful.ps1 -Project aegis-batch4-validation
./scripts/validate-observability.ps1 -Project aegis-batch4-validation
```

Use `Scripts/python` with standard Windows Python. The Batch 4 script saves
normalized traces, graphs, trace IDs, and actual parent/child evidence to
`validation-artifacts/batch4/`. Graph expectations are assertions in the validation
script, never rules in the analyzer. It exercises both Jaeger query endpoints and
compares live CLI JSON with offline fixture replay.

For Batch 5, use the existing Compose file in an isolated project:

```powershell
docker compose -p aegis-batch5-validation build
analysis/.venv/bin/python scripts/validate-evidence.py
./scripts/validate-stateful.ps1 -Project aegis-batch5-validation
./scripts/validate-observability.ps1 -Project aegis-batch5-validation
```

The new validator generates two successful checkouts and one deliberate Payment
failure, consuming three `sku-001` units. It verifies exact trace/span provenance,
counts, errors, and caller-latency statistics against the returned telemetry, then
compares live service-query, exact-trace and fixture CLI output with library output.
Artifacts are saved under `validation-artifacts/batch5/`. PostgreSQL is restored in
a finally block and project containers are stopped; named volumes are preserved.
Do not run validation projects concurrently because they use the same host ports.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `model.py` | Typed spans, traces, diagnostics, edges, and graph serialization |
| `sources.py` | `TraceSource` protocol, input errors, normalized fixture reader |
| `jaeger.py` | Jaeger HTTP/file sources and vendor-specific normalization |
| `observations.py` | Shared canonical parent/child observation extraction and provenance identities |
| `graph.py` | Aggregates canonical observations into the existing dependency graph |
| `evidence.py` | Explicit windows, evidence summaries, caller latency and deterministic serialization |
| `cli.py`, `__main__.py` | Argument parsing, input selection, presentation, exit codes |
| `tests/` | Offline domain, adapter, query construction, error, and CLI checks |

Study `Span` and `Trace`, then `normalize_jaeger`,
`reconstruct_dependencies`, `ServiceGraph.to_dict`, and `cli.main` in that order.
See the [architecture and data contract](../docs/architecture/batch-4-trace-analysis.md)
and [validation report](../docs/reports/batch-4.md).
