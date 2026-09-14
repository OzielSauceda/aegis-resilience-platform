import io
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from aegis_analysis.cli import main
from aegis_analysis.graph import reconstruct_dependencies
from aegis_analysis.jaeger import JaegerFileSource, JaegerSource, normalize_jaeger
from aegis_analysis.sources import FixtureSource, SourceError


def payload():
    return {"data": [{"traceID": "t", "processes": {"p": {"serviceName": "caller"}, "q": {"serviceName": "callee"}}, "spans": [
        {"traceID": "t", "spanID": "a", "processID": "p", "operationName": "send", "startTime": 100, "duration": 20, "tags": [{"key": "span.kind", "value": "client"}]},
        {"traceID": "t", "spanID": "b", "processID": "q", "operationName": "receive", "startTime": 101, "duration": 18, "references": [{"refType": "CHILD_OF", "traceID": "t", "spanID": "a"}], "tags": [{"key": "span.kind", "value": "server"}, {"key": "error", "value": True}]}
    ]}]}


def test_adapter_fields_and_error():
    traces = normalize_jaeger(payload())
    span = traces[0].spans[1]
    assert (span.trace_id, span.span_id, span.parent_span_id, span.service_name) == ("t", "b", "a", "callee")
    assert (span.operation_name, span.span_kind, span.start_time_us, span.duration_us, span.status) == ("receive", "server", 101, 18, "error")
    assert span.attributes["error"] is True
    graph = reconstruct_dependencies(traces)
    assert graph.edges[0].error_count == 1
    assert not graph.diagnostics


@pytest.mark.parametrize("refs", [
    [{"refType": "CHILD_OF", "traceID": "other", "spanID": "a"}],
    [{"refType": "CHILD_OF", "traceID": "t", "spanID": "a"}, {"refType": "CHILD_OF", "traceID": "t", "spanID": "z"}],
    [{"refType": "FOLLOWS_FROM", "traceID": "t", "spanID": "a"}],
    [None],
])
def test_unsupported_or_ambiguous_parents_are_not_guessed(refs):
    data = payload()
    data["data"][0]["spans"][1]["references"] = refs
    graph = reconstruct_dependencies(normalize_jaeger(data))
    assert not graph.edges
    assert graph.diagnostics


def test_missing_process_bad_timing_invalid_span():
    data = payload()
    span = data["data"][0]["spans"][1]
    span.update(processID="missing", duration=-1, startTime="bad")
    data["data"][0]["spans"].append(None)
    graph = reconstruct_dependencies(normalize_jaeger(data))
    assert not graph.edges
    assert {d.code for d in graph.diagnostics} == {"unknown_service", "invalid_timing", "invalid_span"}


@pytest.mark.parametrize("data", [None, [], {}, {"data": None}, {"data": [], "errors": ["query failed"]}, {"data": [{"traceID": "t"}]}])
def test_bad_envelopes_fail(data):
    with pytest.raises(SourceError):
        normalize_jaeger(data)


def test_live_source_url_encoding_timeout_and_normalization(monkeypatch):
    def open_response(url, timeout):
        parsed = urlsplit(url)
        assert parsed.path == "/api/traces"
        params = parse_qs(parsed.query)
        assert params["service"] == ["a & b"]
        assert params["limit"] == ["4"]
        assert json.loads(params["tags"][0]) == {"order": "x&y"}
        assert timeout == 3
        return io.BytesIO(json.dumps(payload()).encode())
    monkeypatch.setattr("aegis_analysis.jaeger.urlopen", open_response)
    assert len(JaegerSource(service="a & b", limit=4, tags={"order": "x&y"}, timeout=3).read_traces()) == 1
    assert JaegerSource(trace_id="x/y").url.endswith("/api/traces/x%2Fy")


def test_source_network_failure_is_explicit(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("unavailable")
    monkeypatch.setattr("aegis_analysis.jaeger.urlopen", fail)
    with pytest.raises(SourceError, match="unavailable"):
        JaegerSource(service="caller").read_traces()


@pytest.mark.parametrize("kwargs", [{}, {"service": "a", "trace_id": "b"}, {"service": "a", "limit": 0}, {"service": "a", "timeout": float("nan")}, {"service": "a", "url": "file:///tmp/x"}, {"service": "a", "url": "http://["}])
def test_query_validation(kwargs):
    with pytest.raises(SourceError):
        JaegerSource(**kwargs)


def test_exported_bom_file_and_cli(tmp_path, capsys):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload()["data"][0]), encoding="utf-8-sig")
    expected = reconstruct_dependencies(JaegerFileSource(path).read_traces()).to_json()
    assert main(["dependencies", "--jaeger-file", str(path), "--json"]) == 0
    assert capsys.readouterr().out == expected
    assert main(["dependencies", "--jaeger-file", str(path)]) == 0
    assert "caller -> callee: 1 observations, 1 errors" in capsys.readouterr().out


def test_normalized_fixture_cli_and_invalid_file(tmp_path, capsys):
    fixture = Path(__file__).parent / "fixtures" / "branching.json"
    assert main(["dependencies", "--fixture", str(fixture), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["edges"]) == 2
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 1, "traces": [{"trace_id":"t","spans":[{}]}]}')
    with pytest.raises(SourceError):
        FixtureSource(invalid).read_traces()
    assert main(["dependencies", "--fixture", str(invalid)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Cannot read normalized fixture" in output.err


def test_empty_query_json_and_warning(monkeypatch, capsys):
    monkeypatch.setattr(JaegerSource, "read_traces", lambda self: ())
    assert main(["dependencies", "--service", "caller", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["nodes"] == []
    assert "No traces returned" in captured.err


@pytest.mark.parametrize("status_tag,expected", [("ERROR", "error"), (2, "error"), ("OK", "ok"), (1, "ok"), ("unknown", "unset")])
def test_status_normalization(status_tag, expected):
    data = payload()
    data["data"][0]["spans"][0]["tags"] = [{"key": "otel.status_code", "value": status_tag}]
    assert normalize_jaeger(data)[0].spans[0].status == expected


def test_ambiguous_tags_do_not_depend_on_tag_order():
    data = payload()
    tags = [{"key": "error", "value": True}, {"key": "error", "value": False}, {"key": "nested", "value": []}]
    data["data"][0]["spans"][1]["tags"] = tags
    first = normalize_jaeger(data)
    tags.reverse()
    assert normalize_jaeger(data) == first
    assert first[0].spans[1].status == "unset"
    assert first[0].spans[1].attributes == {}
    assert first[0].diagnostics[0].code == "invalid_tags"


def test_malformed_json_and_missing_files(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{broken")
    assert main(["dependencies", "--jaeger-file", str(path), "--json"]) == 1
    assert capsys.readouterr().out == ""
    with pytest.raises(SourceError):
        FixtureSource(tmp_path / "absent").read_traces()


def test_timeout_is_wrapped(monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("timed out")
    monkeypatch.setattr("aegis_analysis.jaeger.urlopen", fail)
    with pytest.raises(SourceError, match="timed out"):
        JaegerSource(service="caller").read_traces()
