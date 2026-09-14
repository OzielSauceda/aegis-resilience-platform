"""Developer CLI; graph JSON goes to stdout, operational failures to stderr."""

import argparse
import sys

from .graph import reconstruct_dependencies
from .jaeger import JaegerFileSource, JaegerSource
from .sources import FixtureSource, SourceError, TraceSource


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aegis")
    commands = parser.add_subparsers(dest="command", required=True)
    dependencies = commands.add_parser("dependencies", help="reconstruct observed service dependencies")
    inputs = dependencies.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--fixture", help="normalized v1 JSON fixture")
    inputs.add_argument("--jaeger-file", help="exported Jaeger JSON")
    inputs.add_argument("--service", help="query Jaeger for this service")
    inputs.add_argument("--trace-id", help="query Jaeger for one trace")
    dependencies.add_argument("--jaeger-url", default="http://127.0.0.1:16686")
    dependencies.add_argument("--lookback", default="1h")
    dependencies.add_argument("--limit", type=int, default=20)
    dependencies.add_argument("--timeout", type=float, default=5)
    dependencies.add_argument("--json", action="store_true", help="emit deterministic graph JSON")
    args = parser.parse_args(argv)
    try:
        source: TraceSource
        if args.fixture:
            source = FixtureSource(args.fixture)
        elif args.jaeger_file:
            source = JaegerFileSource(args.jaeger_file)
        else:
            source = JaegerSource(args.jaeger_url, service=args.service, trace_id=args.trace_id,
                                  lookback=args.lookback, limit=args.limit, timeout=args.timeout)
        traces = source.read_traces()
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
    except SourceError as exc:
        print(f"aegis: {exc}", file=sys.stderr)
        return 1
