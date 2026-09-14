"""Reconstruct only direct, observed cross-service parent/child relationships."""

from collections import defaultdict
from collections.abc import Iterable

from .model import Diagnostic, Edge, ServiceGraph, Span, Trace


def reconstruct_dependencies(traces: Iterable[Trace]) -> ServiceGraph:
    # Merge trace fragments before resolving parents; span IDs are trace-local.
    records: dict[tuple[str, str], list[Span]] = defaultdict(list)
    diagnostics: set[Diagnostic] = set()
    for trace in traces:
        diagnostics.update(trace.diagnostics)
        for span in trace.spans:
            if not span.span_id or not trace.trace_id or span.trace_id != trace.trace_id:
                diagnostics.add(Diagnostic("invalid_identity", trace.trace_id, span.span_id))
                continue
            records[(trace.trace_id, span.span_id)].append(span)

    indexed: dict[tuple[str, str], Span] = {}
    for key, copies in records.items():
        if any(copy != copies[0] for copy in copies[1:]):
            # No arrival-order winner: conflicting identities cannot supply evidence.
            diagnostics.add(Diagnostic("conflicting_duplicate", *key))
        else:
            indexed[key] = copies[0]

    # Invalid cycles cannot support ancestry. Iterative traversal avoids recursion limits.
    cyclic: set[tuple[str, str]] = set()
    visited: set[tuple[str, str]] = set()
    for key in indexed:
        path: dict[tuple[str, str], int] = {}
        cursor = key
        while cursor in indexed and cursor not in visited:
            if cursor in path:
                cyclic.update(list(path)[path[cursor]:])
                break
            path[cursor] = len(path)
            parent_id = indexed[cursor].parent_span_id
            if parent_id is None:
                break
            cursor = (cursor[0], parent_id)
        visited.update(path)
    for key in cyclic:
        diagnostics.add(Diagnostic("cyclic_parent", *key))

    nodes: set[str] = set()
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for key, span in indexed.items():
        service = span.service_name
        if service is None or not service.strip():
            diagnostics.add(Diagnostic("unknown_service", *key))
        else:
            nodes.add(service)
        if span.parent_span_id is None:
            continue
        parent_key = (span.trace_id, span.parent_span_id)
        parent = indexed.get(parent_key)
        if parent is None:
            diagnostics.add(Diagnostic("missing_parent", *key))
            continue
        if key in cyclic or parent_key in cyclic:
            continue
        if not service or not service.strip() or not parent.service_name or not parent.service_name.strip():
            continue
        if service == parent.service_name:
            continue
        totals = counts[(parent.service_name, service)]
        totals[0] += 1
        # Child status belongs to this observation; an ancestor's error may be
        # caused by a different branch and must not contaminate this edge.
        totals[1] += int(span.status == "error")

    edges = tuple(Edge(source, target, *totals) for (source, target), totals in sorted(counts.items()))
    return ServiceGraph(tuple(sorted(nodes)), edges, tuple(sorted(diagnostics)))
