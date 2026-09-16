"""Developer CLI; analytical JSON goes to stdout, operational failures to stderr."""

import argparse
import sys

from .graph import reconstruct_dependencies
from .evidence import EvidenceSummary, TimeWindow, summarize_evidence
from .jaeger import JaegerFileSource, JaegerSource
from .sources import FixtureSource, TraceSource


def _source_arguments(command: argparse.ArgumentParser) -> None:
    inputs = command.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--fixture", help="normalized v1 JSON fixture")
    inputs.add_argument("--jaeger-file", help="exported Jaeger JSON")
    inputs.add_argument("--service", help="query Jaeger for this service")
    inputs.add_argument("--trace-id", help="query Jaeger for one trace")
    command.add_argument("--jaeger-url", default="http://127.0.0.1:16686")
    command.add_argument("--lookback", default="1h")
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--timeout", type=float, default=5)
    command.add_argument("--json", action="store_true", help="emit deterministic JSON")


def _source_from_args(args: argparse.Namespace) -> TraceSource:
    if args.fixture:
        return FixtureSource(args.fixture)
    if args.jaeger_file:
        return JaegerFileSource(args.jaeger_file)
    return JaegerSource(args.jaeger_url, service=args.service, trace_id=args.trace_id,
                        lookback=args.lookback, limit=args.limit, timeout=args.timeout)


def _print_evidence(summary: EvidenceSummary) -> None:
    print(f"Window: [{summary.window.start_us}, {summary.window.end_us}) microseconds")
    if not summary.edges:
        print("No qualifying observations were available in this evidence/window.")
    for edge in summary.edges:
        latency = edge.latency_us
        print(f"{edge.source} -> {edge.target}")
        print(f"  observations: {edge.observation_count}")
        print(f"  errors: {edge.error_count} ({edge.error_rate:.2%})")
        print(f"  latency samples: {latency.sample_count}")
        for label in ("average", "p50", "p95"):
            value = getattr(latency, label)
            print(f"  {label}: {value if value is not None else 'unavailable'} us")
        for ref in edge.provenance:
            print(f"  evidence: trace={ref.trace_id} caller={ref.caller_span_id} callee={ref.callee_span_id}")
    for limitation in summary.to_dict()["limitations"]:
        print(f"Limitation: {limitation}", file=sys.stderr)
    for item in summary.diagnostics:
        print(f"Warning: {item.code} trace={item.trace_id} span={item.span_id}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aegis")
    commands = parser.add_subparsers(dest="command", required=True)
    dependencies = commands.add_parser("dependencies", help="reconstruct observed service dependencies")
    evidence = commands.add_parser("evidence", help="summarize observed evidence in an explicit time window")
    for command in (dependencies, evidence):
        _source_arguments(command)
    evidence.add_argument("--start-us", type=int, required=True)
    evidence.add_argument("--end-us", type=int, required=True)
    args = parser.parse_args(argv)
    window = None
    if args.command == "evidence":
        try:
            window = TimeWindow(args.start_us, args.end_us)
        except ValueError as exc:
            evidence.error(str(exc))
    try:
        traces = _source_from_args(args).read_traces()
        if window is not None:
            summary = summarize_evidence(traces, window)
            if args.json:
                print(summary.to_json(), end="")
            else:
                _print_evidence(summary)
            return 0
        graph = reconstruct_dependencies(traces)
        if not traces:
            print("No traces returned; the empty graph does not establish absence of dependencies.", file=sys.stderr)
        if args.json:
            print(graph.to_json(), end="")
        else:
            print("Services: " + (", ".join(graph.nodes) or "(none observed)"))
            for edge in graph.edges:
                print(f"{edge.source} -> {edge.target}: {edge.request_count} observations, {edge.error_count} errors")
            for item in graph.diagnostics:
                print(f"Warning: {item.code} trace={item.trace_id} span={item.span_id}")
        return 0
    except ValueError as exc:
        print(f"aegis: {exc}", file=sys.stderr)
        return 1
