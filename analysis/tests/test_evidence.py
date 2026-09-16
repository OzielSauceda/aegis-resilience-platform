from dataclasses import replace
import json
from pathlib import Path
import random

import pytest

from aegis_analysis import Span, Trace, TimeWindow, reconstruct_dependencies, summarize_evidence
from aegis_analysis.model import Diagnostic
from aegis_analysis.observations import EvidenceReference, extract_observations
from aegis_analysis.sources import FixtureSource

FIXTURE = Path(__file__).parent / "fixtures" / "timed-calls.json"
WINDOW = TimeWindow(1000, 2000)


def call(trace_id="t", *, start=1000, duration=100, status="unset"):
    return Trace(trace_id, (
        Span(trace_id, "p", None, "caller", span_kind="client", start_time_us=900, duration_us=duration),
        Span(trace_id, "c", "p", "callee", span_kind="server", start_time_us=start, duration_us=7, status=status),
    ))


def test_valid_window_and_half_open_membership():
    assert WINDOW.contains(1000)
    assert WINDOW.contains(1999)
    assert not WINDOW.contains(999)
    assert not WINDOW.contains(2000)


@pytest.mark.parametrize("start,end", [(1, 1), (2, 1), (True, 2), (1, False), (1.0, 2), (1, 2.0), (None, 2), (1, float("inf"))])
def test_invalid_windows_fail(start, end):
    with pytest.raises(ValueError):
        TimeWindow(start, end)


def test_child_timestamp_anchors_adjacent_windows_not_parent_or_completion():
    trace = call(start=1000, duration=2000)
    assert not summarize_evidence([trace], TimeWindow(0, 1000)).edges
    edge = summarize_evidence([trace], TimeWindow(1000, 1001)).edges[0]
    assert edge.observation_count == edge.latency_us.sample_count == 1
    assert edge.latency_us.average == 2000  # No clipping to window width.
    assert not summarize_evidence([trace], TimeWindow(1001, 3000)).edges


def test_counts_errors_caller_latency_and_complete_provenance():
    summary = summarize_evidence(FixtureSource(FIXTURE).read_traces(), WINDOW)
    edge, = summary.edges
    assert (edge.source, edge.target, edge.observation_count, edge.error_count) == ("gateway", "worker", 3, 1)
    assert edge.error_rate == 1 / 3
    assert (edge.latency_us.sample_count, edge.latency_us.average, edge.latency_us.p50, edge.latency_us.p95) == (2, 200, 100, 300)
    assert edge.provenance == (EvidenceReference("t1", "a", "b"), EvidenceReference("t1", "c", "d"), EvidenceReference("t2", "e", "f"))
    assert summary.diagnostics == (Diagnostic("unavailable_caller_duration", "t2", "e"),)


def test_repeated_deliveries_and_fragments_are_deduplicated_before_windowing():
    traces = FixtureSource(FIXTURE).read_traces()
    fragments = [Trace(t.trace_id, (s,)) for t in traces for s in t.spans]
    assert summarize_evidence([*traces, *traces, *fragments], WINDOW) == summarize_evidence(traces, WINDOW)


@pytest.mark.parametrize("which", [0, 1])
def test_conflicting_parent_or_child_remains_quarantined(which):
    trace = call()
    conflicting = replace(trace.spans[which], duration_us=999)
    summary = summarize_evidence([trace, Trace("t", (conflicting,))], WINDOW)
    assert not summary.edges
    assert Diagnostic("conflicting_duplicate", "t", conflicting.span_id) in summary.diagnostics


@pytest.mark.parametrize("field,which", [("start_time_us", 1), ("duration_us", 0)])
@pytest.mark.parametrize("bad", [True, 1.0])
def test_timing_type_conflicts_cannot_depend_on_arrival_order(field, which, bad):
    spans = list(call(start=1, duration=1).spans)
    alternate = replace(spans[which], **{field: bad})
    records = [Trace("t", tuple(spans)), Trace("t", (alternate,))]
    first = summarize_evidence(records, TimeWindow(0, 2))
    assert not first.edges
    assert summarize_evidence(reversed(records), TimeWindow(0, 2)).to_json() == first.to_json()


@pytest.mark.parametrize("timestamp", [None, -1, True, 1000.0, "1000", float("nan"), float("inf")])
def test_missing_or_invalid_timestamp_is_unclassifiable(timestamp):
    trace = call(start=timestamp)
    summary = summarize_evidence([trace], WINDOW)
    assert not summary.edges
    assert summary.diagnostics == (Diagnostic("unclassifiable_observation_time", "t", "c"),)
    assert reconstruct_dependencies([trace]).edges[0].request_count == 1


@pytest.mark.parametrize("duration", [None, -1, True, 100.0, "100", float("nan"), float("inf")])
def test_missing_or_invalid_latency_keeps_observations_errors_and_provenance(duration):
    summary = summarize_evidence([call(duration=duration, status="error")], WINDOW)
    edge, = summary.edges
    assert edge.observation_count == edge.error_count == edge.error_rate == 1
    assert edge.latency_us.sample_count == 0
    assert edge.latency_us.average is edge.latency_us.p50 is edge.latency_us.p95 is None
    assert edge.provenance == (EvidenceReference("t", "p", "c"),)
    document = summary.to_json()
    assert '"average": null' in document
    assert "NaN" not in document and "Infinity" not in document
    assert summary.diagnostics == (Diagnostic("unavailable_caller_duration", "t", "p"),)


@pytest.mark.parametrize("parent_kind,child_kind", [("server", "server"), ("producer", "consumer"), ("client", "internal"), ("unknown", "server")])
def test_non_rpc_relationship_counts_without_fabricating_latency(parent_kind, child_kind):
    trace = call(status="error")
    trace = replace(trace, spans=(replace(trace.spans[0], span_kind=parent_kind), replace(trace.spans[1], span_kind=child_kind)))
    summary = summarize_evidence([trace], WINDOW)
    edge, = summary.edges
    assert edge.observation_count == edge.error_count == 1
    assert edge.latency_us.sample_count == 0
    assert summary.diagnostics == (Diagnostic("inapplicable_edge_latency", "t", "c"),)


@pytest.mark.parametrize("samples,average,p50,p95", [([0], 0, 0, 0), ([37], 37, 37, 37), ([1, 2], 1.5, 1, 2), ([1, 2, 3, 4, 100], 22, 3, 100), (list(range(1, 21)), 10.5, 10, 19)])
def test_arithmetic_mean_and_nearest_rank_samples(samples, average, p50, p95):
    traces = [call(str(i), duration=value) for i, value in enumerate(samples)]
    edge, = summarize_evidence(traces, WINDOW).edges
    assert (edge.latency_us.sample_count, edge.latency_us.average, edge.latency_us.p50, edge.latency_us.p95) == (len(samples), average, p50, p95)


def test_ancestor_and_sibling_errors_do_not_contaminate_healthy_edge():
    trace = call()
    parent, child = trace.spans
    root = Span("t", "root", None, "caller", status="error")
    sibling = Span("t", "sibling", "root", "other", start_time_us=1000, status="error")
    trace = replace(trace, spans=(root, replace(parent, parent_span_id="root", status="error"), child, sibling))
    result = {(e.source, e.target): e for e in summarize_evidence([trace], WINDOW).edges}
    assert result[("caller", "callee")].error_count == 0
    assert result[("caller", "other")].error_count == 1


@pytest.mark.parametrize("problem", ["missing_parent", "unknown_parent", "unknown_child", "cycle", "same_service", "foreign_trace"])
def test_integrity_rules_match_graph_and_never_invent_edges(problem):
    parent, child = call().spans
    if problem == "missing_parent":
        traces = [Trace("t", (child,))]
    elif problem == "unknown_parent":
        traces = [Trace("t", (replace(parent, service_name=None), child))]
    elif problem == "unknown_child":
        traces = [Trace("t", (parent, replace(child, service_name=" ")))]
    elif problem == "cycle":
        traces = [Trace("t", (replace(parent, parent_span_id="c"), child))]
    elif problem == "same_service":
        traces = [Trace("t", (parent, replace(child, service_name="caller")))]
    else:
        traces = [Trace("other", (replace(parent, trace_id="other"),)), Trace("t", (child,))]
    graph = reconstruct_dependencies(traces)
    summary = summarize_evidence(traces, WINDOW)
    assert not graph.edges and not summary.edges
    assert summary.diagnostics == graph.diagnostics


def test_normalization_diagnostics_preserved_and_outside_latency_not_reported():
    trace = call(start=2000, duration=None)
    diagnostic = Diagnostic("invalid_timing", "t", "p")
    trace = replace(trace, diagnostics=(diagnostic, diagnostic))
    summary = summarize_evidence([trace], WINDOW)
    assert not summary.edges
    assert summary.diagnostics == (diagnostic,)


def test_shuffle_trace_span_duplicate_and_provenance_order_is_byte_identical():
    traces = list(FixtureSource(FIXTURE).read_traces())
    traces += [call("z", start=None), call("different", status="error")]
    expected = summarize_evidence(traces, WINDOW).to_json()
    rng = random.Random(5)
    for _ in range(25):
        shuffled = []
        for trace in traces * 2:
            spans = list(trace.spans)
            rng.shuffle(spans)
            shuffled.append(replace(trace, spans=tuple(spans)))
        rng.shuffle(shuffled)
        assert summarize_evidence(iter(shuffled), WINDOW).to_json() == expected


def test_graph_counts_and_extracted_observations_match_all_time_summary():
    traces = FixtureSource(FIXTURE).read_traces()
    graph = reconstruct_dependencies(traces)
    summary = summarize_evidence(traces, TimeWindow(0, 10000))
    assert {(e.source, e.target): (e.request_count, e.error_count) for e in graph.edges} == {(e.source, e.target): (e.observation_count, e.error_count) for e in summary.edges}
    refs = tuple(item.reference for item in extract_observations(traces).observations)
    assert summary.edges[0].provenance == refs


def test_empty_summary_still_declares_unknown_completeness():
    result = json.loads(summarize_evidence([], WINDOW).to_json())
    assert result == {"schema_version": 1, "window": {"start_us": 1000, "end_us": 2000}, "edges": [], "diagnostics": [], "limitations": ["observed_telemetry_only", "sampling_and_query_completeness_unknown"]}
