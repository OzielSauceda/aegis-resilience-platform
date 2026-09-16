"""Aggregate canonical observations into the unchanged Batch 4 graph contract."""

from collections import defaultdict
from collections.abc import Iterable

from .model import Edge, ServiceGraph, Trace
from .observations import extract_observations


def reconstruct_dependencies(traces: Iterable[Trace]) -> ServiceGraph:
    batch = extract_observations(traces)
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for observation in batch.observations:
        totals = counts[(observation.source, observation.target)]
        totals[0] += 1
        totals[1] += int(observation.is_error)

    edges = tuple(Edge(source, target, *totals) for (source, target), totals in sorted(counts.items()))
    return ServiceGraph(batch.nodes, edges, batch.diagnostics)
