"""Deterministic summaries of available telemetry, never traffic completeness claims."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
import json
from statistics import mean

from .model import Diagnostic, Trace
from .observations import CrossServiceObservation, EvidenceReference, extract_observations


@dataclass(frozen=True)
class TimeWindow:
    """Epoch microsecond interval [start_us, end_us); adjacent windows never overlap."""

    start_us: int
    end_us: int

    def __post_init__(self) -> None:
        if type(self.start_us) is not int or type(self.end_us) is not int:
            raise ValueError("window boundaries must be integer epoch microseconds")
        if self.start_us >= self.end_us:
            raise ValueError("window start_us must be less than end_us")

    def contains(self, timestamp_us: int) -> bool:
        return self.start_us <= timestamp_us < self.end_us


@dataclass(frozen=True)
class LatencySummary:
    sample_count: int
    average: int | float | None
    p50: int | None
    p95: int | None


@dataclass(frozen=True)
class EdgeEvidence:
    source: str
    target: str
    observation_count: int
    error_count: int
    error_rate: float
    latency_us: LatencySummary
    provenance: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class EvidenceSummary:
    window: TimeWindow
    edges: tuple[EdgeEvidence, ...]
    diagnostics: tuple[Diagnostic, ...]

    def to_dict(self) -> dict:
        edges = []
        for edge in sorted(self.edges, key=lambda item: (item.source, item.target)):
            record = asdict(edge)
            record["provenance"] = [asdict(ref) for ref in sorted(edge.provenance)]
            edges.append(record)
        return {
            "schema_version": 1,
            "window": asdict(self.window),
            "edges": edges,
            # Neither normalized traces nor query results establish a sampling rate.
            "limitations": ["observed_telemetry_only", "sampling_and_query_completeness_unknown"],
            "diagnostics": [asdict(item) for item in sorted(set(self.diagnostics))],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"


def _valid_time(value: object) -> bool:
    # Match the normalized source convention; bool is not a timestamp/duration.
    return type(value) is int and value >= 0


def _latency_summary(samples: list[int]) -> LatencySummary:
    if not samples:
        return LatencySummary(0, None, None, None)
    ordered = sorted(samples)
    count = len(ordered)
    # Nearest rank: ceil(p*N), using exact integer arithmetic and 1-based ranks.
    p50 = ordered[(50 * count + 99) // 100 - 1]
    p95 = ordered[(95 * count + 99) // 100 - 1]
    try:
        average = mean(ordered)
    except OverflowError as exc:
        raise ValueError("latency average exceeds the finite numeric output range") from exc
    return LatencySummary(count, average, p50, p95)


def summarize_evidence(traces: Iterable[Trace], window: TimeWindow) -> EvidenceSummary:
    batch = extract_observations(traces)
    diagnostics = set(batch.diagnostics)
    grouped: dict[tuple[str, str], list[CrossServiceObservation]] = defaultdict(list)
    for observation in batch.observations:
        child = observation.child
        # Membership belongs to the callee operation establishing the relationship.
        if not _valid_time(child.start_time_us):
            diagnostics.add(Diagnostic("unclassifiable_observation_time", child.trace_id, child.span_id))
            continue
        if window.contains(child.start_time_us):
            grouped[(observation.source, observation.target)].append(observation)

    edges = []
    for (source, target), observations in sorted(grouped.items()):
        samples = []
        for observation in observations:
            parent, child = observation.parent, observation.child
            # Caller CLIENT duration measures experienced downstream call latency.
            # SERVER processing time is a different measurement, never a fallback.
            if parent.span_kind != "client" or child.span_kind != "server":
                diagnostics.add(Diagnostic("inapplicable_edge_latency", child.trace_id, child.span_id))
            elif not _valid_time(parent.duration_us):
                diagnostics.add(Diagnostic("unavailable_caller_duration", parent.trace_id, parent.span_id))
            else:
                samples.append(parent.duration_us)
        count = len(observations)
        errors = sum(observation.is_error for observation in observations)
        # Missing latency does not remove counts, explicit errors, or provenance.
        edges.append(EdgeEvidence(source, target, count, errors, errors / count,
                                  _latency_summary(samples),
                                  tuple(sorted(item.reference for item in observations))))
    return EvidenceSummary(window, tuple(edges), tuple(sorted(diagnostics)))
