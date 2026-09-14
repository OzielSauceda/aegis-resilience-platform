from dataclasses import replace
from itertools import permutations
from pathlib import Path
import random

from aegis_analysis import Span, Trace, reconstruct_dependencies
from aegis_analysis.sources import FixtureSource

FIXTURE = Path(__file__).parent / "fixtures" / "branching.json"


def edges(graph):
    return {(e.source, e.target): (e.request_count, e.error_count) for e in graph.edges}


def test_branching_http_and_datastore_spans():
    graph = reconstruct_dependencies(FixtureSource(FIXTURE).read_traces())
    assert graph.nodes == ("checkout", "inventory", "payment")
    assert edges(graph) == {("checkout", "inventory"): (1, 0), ("checkout", "payment"): (1, 0)}
    assert not graph.diagnostics


def test_multiple_traces_fragments_and_duplicate_batches():
    original = FixtureSource(FIXTURE).read_traces()[0]
    second = Trace("second", tuple(replace(s, trace_id="second") for s in original.spans))
    fragments = [Trace(original.trace_id, original.spans[:3]), Trace(original.trace_id, original.spans[3:])]
    graph = reconstruct_dependencies([original, original, second, *fragments])
    assert edges(graph) == {("checkout", "inventory"): (2, 0), ("checkout", "payment"): (2, 0)}
    assert not graph.diagnostics


def test_arbitrary_input_order_is_byte_identical():
    trace = FixtureSource(FIXTURE).read_traces()[0]
    expected = reconstruct_dependencies([trace]).to_json()
    rng = random.Random(42)
    for _ in range(30):
        spans = list(trace.spans)
        rng.shuffle(spans)
        assert reconstruct_dependencies([Trace(trace.trace_id, tuple(spans))]).to_json() == expected


def test_unknown_service_and_missing_parent_do_not_bridge_ancestors():
    trace = Trace("t", (
        Span("t", "a", None, "gateway"),
        Span("t", "b", "a", None),
        Span("t", "c", "b", "worker"),
        Span("t", "d", "missing", "worker"),
    ))
    graph = reconstruct_dependencies([trace])
    assert not graph.edges
    assert {(d.code, d.span_id) for d in graph.diagnostics} == {("unknown_service", "b"), ("missing_parent", "d")}


def test_single_service_and_empty_batch():
    assert not reconstruct_dependencies([]).nodes
    graph = reconstruct_dependencies([Trace("t", (Span("t", "a", None, "only"), Span("t", "b", "a", "only")))])
    assert graph.nodes == ("only",)
    assert not graph.edges


def test_failure_belongs_only_to_downstream_observation():
    trace = FixtureSource(FIXTURE).read_traces()[0]
    trace = replace(trace, spans=tuple(replace(s, status="error") if s.span_id in {"1", "5", "6", "7"} else s for s in trace.spans))
    assert edges(reconstruct_dependencies([trace])) == {("checkout", "inventory"): (1, 0), ("checkout", "payment"): (1, 1)}


def test_conflicting_duplicates_are_quarantined_regardless_of_order():
    spans = (Span("t", "a", None, "caller"), Span("t", "b", "a", "worker"), Span("t", "b", "a", "different"), Span("t", "c", "b", "leaf"))
    outputs = {reconstruct_dependencies([Trace("t", order)]).to_json() for order in permutations(spans)}
    assert len(outputs) == 1
    graph = reconstruct_dependencies([Trace("t", spans)])
    assert not graph.edges
    assert {d.code for d in graph.diagnostics} == {"conflicting_duplicate", "missing_parent"}


def test_span_ids_never_join_across_traces():
    graph = reconstruct_dependencies([Trace("one", (Span("one", "a", None, "caller"),)), Trace("two", (Span("two", "b", "a", "callee"),))])
    assert not graph.edges
    assert graph.diagnostics[0].code == "missing_parent"


def test_service_names_and_operation_names_are_not_topology_rules():
    graph = reconstruct_dependencies([Trace("t", (Span("t", "x", None, "alpha", "payment"), Span("t", "y", "x", "beta", "redis.reserve")))])
    assert edges(graph) == {("alpha", "beta"): (1, 0)}


def test_cycles_self_parent_and_mismatched_identity():
    spans = (Span("t", "a", "b", "a"), Span("t", "b", "a", "b"), Span("t", "c", "c", "c"), Span("other", "d", "a", "d"))
    graph = reconstruct_dependencies([Trace("t", spans)])
    assert not graph.edges
    assert [d.code for d in graph.diagnostics].count("cyclic_parent") == 3
    assert any(d.code == "invalid_identity" for d in graph.diagnostics)


def test_repeated_distinct_calls_count_separately():
    graph = reconstruct_dependencies([Trace("t", (Span("t", "a", None, "caller"), Span("t", "b", "a", "worker"), Span("t", "c", "a", "worker", status="error")))])
    assert edges(graph) == {("caller", "worker"): (2, 1)}


def test_trace_order_and_diagnostic_order_are_deterministic():
    first = Trace("one", (Span("one", "a", "missing", "caller"),))
    second = Trace("two", (Span("two", "b", None, "   "),))
    graph = reconstruct_dependencies([first, second])
    assert reconstruct_dependencies([second, first]).to_json() == graph.to_json()
    assert graph.nodes == ("caller",)
    assert {d.code for d in graph.diagnostics} == {"missing_parent", "unknown_service"}
