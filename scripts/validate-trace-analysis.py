"""Explicit live Batch 4 validation. Requires installed aegis-analysis and Docker.

Uses the existing Compose file and a separate project; preserves named volumes.
Topology expectations belong in this validation script, never the analyzer.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from aegis_analysis import reconstruct_dependencies
from aegis_analysis.jaeger import JaegerSource
from aegis_analysis.sources import SourceError

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="aegis-batch4-validation")
    args = parser.parse_args()
    evidence = ROOT / "validation-artifacts" / "batch4"
    evidence.mkdir(parents=True, exist_ok=True)

    def compose(*arguments):
        return subprocess.run(["docker", "compose", "-p", args.project, *arguments], cwd=ROOT,
                              check=True, capture_output=True, text=True).stdout.strip()

    def checkout(order, expected):
        body = {"order_id": order, "sku": "sku-001", "quantity": 1, "amount_cents": 2500}
        request = Request("http://127.0.0.1:8080/checkout", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, (response.status, response.read().decode())

    def analyze(order, name, errors):
        traces = ()
        for _ in range(30):
            try:
                candidates = JaegerSource(service="checkout", tags={"shopsim.order_id": order}).read_traces()
                traces = tuple(t for t in candidates if len(t.spans) == 7)
                if len(traces) == 1:
                    break
            except SourceError:
                pass
            time.sleep(1)
        assert len(traces) == 1, f"No unique complete trace for {order}"
        trace = traces[0]
        # Exercise both the search endpoint and the exact trace endpoint.
        exact = JaegerSource(trace_id=trace.trace_id).read_traces()
        graph = reconstruct_dependencies(exact)
        assert graph == reconstruct_dependencies(traces)
        assert graph.nodes == ("checkout", "inventory", "payment"), graph
        assert {(e.source, e.target, e.request_count, e.error_count) for e in graph.edges} == {
            ("checkout", "inventory", 1, 0), ("checkout", "payment", 1, errors)
        }, graph
        assert not graph.diagnostics, graph.diagnostics
        by_id = {span.span_id: span for span in trace.spans}
        relationships = [{"parent_span_id": span.parent_span_id, "child_span_id": span.span_id,
                          "source": by_id[span.parent_span_id].service_name, "target": span.service_name,
                          "child_status": span.status}
                         for span in trace.spans if span.parent_span_id in by_id
                         and by_id[span.parent_span_id].service_name != span.service_name]
        (evidence / f"{name}-graph.json").write_text(graph.to_json(), encoding="utf-8")
        normalized = {"schema_version": 1, "traces": [{"trace_id": trace.trace_id, "spans": [asdict(s) for s in trace.spans]}]}
        fixture_path = evidence / f"{name}-normalized.json"
        fixture_path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
        cli = subprocess.run([sys.executable, "-m", "aegis_analysis", "dependencies", "--trace-id", trace.trace_id, "--json"], check=True, capture_output=True, text=True)
        assert cli.stdout == graph.to_json()
        replay = subprocess.run([sys.executable, "-m", "aegis_analysis", "dependencies", "--fixture", str(fixture_path), "--json"], check=True, capture_output=True, text=True)
        assert replay.stdout == cli.stdout
        result = {"trace_id": trace.trace_id, "order_id": order, "span_count": len(trace.spans), "relationships": sorted(relationships, key=lambda r: r["target"])}
        (evidence / f"{name}-evidence.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(f"PASS {name}: " + json.dumps(result), flush=True)

    try:
        compose("up", "-d", "--wait")
        order = "batch4-" + uuid4().hex
        checkout(order, 200)
        analyze(order, "success", 0)
        stock = int(compose("exec", "-T", "redis", "redis-cli", "--raw", "GET", "stock:sku-001"))
        try:
            compose("stop", "postgres")
            checkout(order + "-failure", 502)
            analyze(order + "-failure", "postgres-failure", 1)
            assert int(compose("exec", "-T", "redis", "redis-cli", "--raw", "GET", "stock:sku-001")) == stock - 1
            print("PASS failed checkout retains inventory reservation", flush=True)
        finally:
            compose("start", "postgres")
        for _ in range(30):
            try:
                with urlopen("http://127.0.0.1:8081/readyz", timeout=2):
                    break
            except (URLError, OSError):
                time.sleep(1)
        checkout(order + "-failure", 200)
        assert int(compose("exec", "-T", "redis", "redis-cli", "--raw", "GET", "stock:sku-001")) == stock - 1
        print("PASS recovery replay does not reserve twice", flush=True)
        print("All Batch 4 live validation checks passed.", flush=True)
    finally:
        compose("down")


if __name__ == "__main__":
    main()
