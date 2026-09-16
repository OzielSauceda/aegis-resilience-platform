"""Canonical cross-service evidence shared by graph and windowed analysis."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .model import Diagnostic, Span, Trace


@dataclass(frozen=True, order=True)
class EvidenceReference:
    trace_id: str
    caller_span_id: str
    callee_span_id: str


@dataclass(frozen=True)
class CrossServiceObservation:
    source: str
    target: str
    parent: Span
    child: Span

    @property
    def reference(self) -> EvidenceReference:
        return EvidenceReference(self.child.trace_id, self.parent.span_id, self.child.span_id)

    @property
    def is_error(self) -> bool:
        # Ancestors and siblings can fail for reasons unrelated to this callee.
        return self.child.status == "error"


@dataclass(frozen=True)
class ObservationBatch:
    nodes: tuple[str, ...]
    observations: tuple[CrossServiceObservation, ...]
    diagnostics: tuple[Diagnostic, ...]


def extract_observations(traces: Iterable[Trace]) -> ObservationBatch:
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
        if any(copy != copies[0]
               or type(copy.start_time_us) is not type(copies[0].start_time_us)
               or type(copy.duration_us) is not type(copies[0].duration_us)
               for copy in copies[1:]):
            # Timing types matter: True and 1 compare equal in Python but only
            # the integer is valid evidence. Never choose by arrival order.
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
    observations = []
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
        # The relationship is the observation, not two independent span samples.
        observations.append(CrossServiceObservation(parent.service_name, service, parent, span))

    return ObservationBatch(tuple(sorted(nodes)),
                            tuple(sorted(observations, key=lambda item: item.reference)),
                            tuple(sorted(diagnostics)))
