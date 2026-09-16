"""Live Batch 5 validation using the existing Compose/Jaeger mechanisms.

Creates two successful checkouts and one PostgreSQL-outage checkout. Preserves
named volumes, restores PostgreSQL, and stops the isolated project on exit.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from aegis_analysis import TimeWindow, reconstruct_dependencies, summarize_evidence
from aegis_analysis.jaeger import JaegerSource
from aegis_analysis.sources import SourceError

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="aegis-batch5-validation")
    args = parser.parse_args()
    artifacts = ROOT / "validation-artifacts" / "batch5"
    artifacts.mkdir(parents=True, exist_ok=True)

    def compose(*arguments):
        result = subprocess.run(["docker", "compose", "-p", args.project, *arguments],
                                cwd=ROOT, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"Compose {arguments} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip()

    def checkout(order, expected):
        body = {"order_id": order, "sku": "sku-001", "quantity": 1, "amount_cents": 2500}
        request = Request("http://127.0.0.1:8080/checkout", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, (response.status, response.read())

    def wait_trace(order):
        last_error = "no complete trace"
        for _ in range(30):
            try:
                found = JaegerSource(service="checkout", tags={"shopsim.order_id": order}).read_traces()
                complete = tuple(trace for trace in found if len(trace.spans) == 7)
                if len(complete) == 1:
                    return complete[0]
            except SourceError as exc:
                last_error = str(exc)
            time.sleep(1)
        raise AssertionError(f"No unique complete trace for {order}: {last_error}")

    try:
        compose("up", "-d", "--wait")
        prefix = "batch5-" + uuid4().hex
        orders = [prefix + suffix for suffix in ("-success-1", "-success-2", "-failure")]
        traces = []
        for order in orders[:2]:
            checkout(order, 200)
            traces.append(wait_trace(order))
        stock_before = int(compose("exec", "-T", "redis", "redis-cli", "--raw", "GET", "stock:sku-001"))
        try:
            compose("stop", "postgres")
            checkout(orders[2], 502)
            traces.append(wait_trace(orders[2]))
            assert int(compose("exec", "-T", "redis", "redis-cli", "--raw", "GET", "stock:sku-001")) == stock_before - 1
        finally:
            compose("start", "postgres")

        # Derive a containing window from actual returned clocks, not host-clock
        # assumptions. Epoch timestamps remain intact in the exported fixtures.
        starts = [span.start_time_us for trace in traces for span in trace.spans]
        assert all(type(value) is int for value in starts)
        window = TimeWindow(min(starts), max(starts) + 1)
        summary = summarize_evidence(traces, window)
        assert not summary.diagnostics, summary.diagnostics
        assert {(e.source, e.target, e.observation_count, e.error_count, e.latency_us.sample_count)
                for e in summary.edges} == {
            ("checkout", "inventory", 3, 0, 3), ("checkout", "payment", 3, 1, 3)
        }
        assert reconstruct_dependencies(traces).nodes == ("checkout", "inventory", "payment")

        # Verify each reported reference against actual returned parent/child data,
        # then calculate independent expected statistics from the caller durations.
        spans = {(span.trace_id, span.span_id): span for trace in traces for span in trace.spans}
        checked_samples = {}
        for edge in summary.edges:
            durations, errors = [], 0
            assert len(edge.provenance) == len(set(edge.provenance)) == 3
            for ref in edge.provenance:
                parent = spans[(ref.trace_id, ref.caller_span_id)]
                child = spans[(ref.trace_id, ref.callee_span_id)]
                assert child.parent_span_id == parent.span_id
                assert (parent.service_name, child.service_name) == (edge.source, edge.target)
                assert (parent.span_kind, child.span_kind) == ("client", "server")
                assert window.contains(child.start_time_us)
                durations.append(parent.duration_us)
                errors += child.status == "error"
            durations.sort()
            assert edge.error_count == errors
            assert edge.error_rate == errors / 3
            assert edge.latency_us.average == sum(durations) / 3
            assert edge.latency_us.p50 == durations[1]
            assert edge.latency_us.p95 == durations[2]
            checked_samples[f"{edge.source}->{edge.target}"] = durations

        retrieved = JaegerSource(service="checkout", limit=100).read_traces()
        assert {t.trace_id for t in retrieved} == {t.trace_id for t in traces}, "Unexpected traces in isolated query"
        assert summarize_evidence(retrieved, window).to_json() == summary.to_json()
        window_args = ["--start-us", str(window.start_us), "--end-us", str(window.end_us)]
        cli = subprocess.run([sys.executable, "-m", "aegis_analysis", "evidence", "--service", "checkout", "--limit", "100", *window_args, "--json"], check=True, capture_output=True, text=True)
        assert cli.stdout == summary.to_json()
        for trace in traces:
            exact = subprocess.run([sys.executable, "-m", "aegis_analysis", "evidence", "--trace-id", trace.trace_id, *window_args, "--json"], check=True, capture_output=True, text=True)
            assert exact.stdout == summarize_evidence([trace], window).to_json()

        normalized = {"schema_version": 1, "traces": [{"trace_id": t.trace_id, "spans": [asdict(s) for s in t.spans]} for t in traces]}
        fixture = artifacts / "normalized.json"
        fixture.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
        replay = subprocess.run([sys.executable, "-m", "aegis_analysis", "evidence", "--fixture", str(fixture), *window_args, "--json"], check=True, capture_output=True, text=True)
        assert replay.stdout == cli.stdout
        (artifacts / "evidence.json").write_text(summary.to_json(), encoding="utf-8")
        receipt = {"orders": orders, "trace_ids": [t.trace_id for t in traces],
                   "queried_trace_count": len(retrieved), "window": asdict(window),
                   "caller_durations_us": checked_samples}
        (artifacts / "validation.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(receipt, indent=2), flush=True)
        print(summary.to_json(), end="", flush=True)
        print("All Batch 5 live evidence validation checks passed.", flush=True)
    finally:
        compose("down")


if __name__ == "__main__":
    main()
