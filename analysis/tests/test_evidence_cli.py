import json
from pathlib import Path

import pytest

from aegis_analysis import TimeWindow, summarize_evidence
from aegis_analysis.cli import main
from aegis_analysis.jaeger import JaegerSource
from aegis_analysis.sources import FixtureSource

FIXTURE = Path(__file__).parent / "fixtures" / "timed-calls.json"
ARGS = ["evidence", "--fixture", str(FIXTURE), "--start-us", "1000", "--end-us", "2000"]


def test_fixture_json_equals_library_and_has_no_stdout_noise(capsys):
    assert main([*ARGS, "--json"]) == 0
    output = capsys.readouterr()
    assert output.out == summarize_evidence(FixtureSource(FIXTURE).read_traces(), TimeWindow(1000, 2000)).to_json()
    assert output.err == ""


def test_human_evidence_and_stderr_limitations(capsys):
    assert main(ARGS) == 0
    output = capsys.readouterr()
    assert "Window: [1000, 2000)" in output.out
    assert "observations: 3" in output.out
    assert "errors: 1 (33.33%)" in output.out
    assert "latency samples: 2" in output.out
    assert "average: 200 us" in output.out
    assert "trace=t1 caller=c callee=d" in output.out
    assert "sampling_and_query_completeness_unknown" in output.err
    assert "unavailable_caller_duration" in output.err


@pytest.mark.parametrize("window_args", [[], ["--start-us", "1"], ["--start-us", "1", "--end-us", "1"], ["--start-us", "3", "--end-us", "2"], ["--start-us", "1.5", "--end-us", "2"]])
def test_window_argument_errors_exit_two(window_args):
    with pytest.raises(SystemExit) as exc:
        main(["evidence", "--fixture", str(FIXTURE), *window_args])
    assert exc.value.code == 2


def test_invalid_window_does_not_attempt_source_read(monkeypatch):
    def forbidden(self):
        pytest.fail("invalid window read its source")
    monkeypatch.setattr(JaegerSource, "read_traces", forbidden)
    with pytest.raises(SystemExit):
        main(["evidence", "--service", "worker", "--start-us", "2", "--end-us", "1"])


@pytest.mark.parametrize("selector", ["--service", "--trace-id"])
def test_evidence_reuses_live_source(monkeypatch, capsys, selector):
    monkeypatch.setattr(JaegerSource, "read_traces", lambda self: FixtureSource(FIXTURE).read_traces())
    assert main(["evidence", selector, "x", "--start-us", "1000", "--end-us", "2000", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["edges"][0]["observation_count"] == 3


def test_empty_query_is_not_reported_as_no_calls(monkeypatch, capsys):
    monkeypatch.setattr(JaegerSource, "read_traces", lambda self: ())
    args = ["evidence", "--service", "x", "--start-us", "1", "--end-us", "2"]
    assert main(args) == 0
    output = capsys.readouterr()
    assert "No qualifying observations were available" in output.out
    assert "observed_telemetry_only" in output.err
    assert main([*args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["limitations"]


def test_evidence_source_failure_preserves_cli_exit_contract(tmp_path, capsys):
    assert main(["evidence", "--fixture", str(tmp_path / "missing"), "--start-us", "1", "--end-us", "2", "--json"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Cannot read normalized fixture" in output.err


def test_jaeger_file_evidence(tmp_path, capsys):
    path = tmp_path / "jaeger.json"
    path.write_text(json.dumps({"traceID": "t", "processes": {"p": {"serviceName": "caller"}, "c": {"serviceName": "callee"}}, "spans": [
        {"traceID": "t", "spanID": "p", "processID": "p", "startTime": 900, "duration": 200, "tags": [{"key": "span.kind", "value": "client"}]},
        {"traceID": "t", "spanID": "c", "processID": "c", "startTime": 1000, "duration": 10, "references": [{"refType": "CHILD_OF", "traceID": "t", "spanID": "p"}], "tags": [{"key": "span.kind", "value": "server"}]}
    ]}), encoding="utf-8-sig")
    assert main(["evidence", "--jaeger-file", str(path), "--start-us", "1000", "--end-us", "2000", "--json"]) == 0
    edge, = json.loads(capsys.readouterr().out)["edges"]
    assert edge["latency_us"] == {"sample_count": 1, "average": 200, "p50": 200, "p95": 200}
