"""Small source boundary; normalized fixtures are independent of any vendor."""

import json
import math
from pathlib import Path
from typing import Protocol

from .model import Span, Trace


class SourceError(ValueError):
    """Input could not supply a trustworthy trace batch."""


class TraceSource(Protocol):
    def read_traces(self) -> tuple[Trace, ...]: ...


class FixtureSource:
    """Read normalized v1 fixtures. Invalid fixtures fail explicitly."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read_traces(self) -> tuple[Trace, ...]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if data["schema_version"] != 1 or not isinstance(data["traces"], list):
                raise ValueError("expected normalized fixture schema version 1")
            traces = []
            for record in data["traces"]:
                trace_id = record["trace_id"]
                if not isinstance(trace_id, str) or not trace_id:
                    raise ValueError("trace_id must be a nonempty string")
                spans = []
                for raw in record["spans"]:
                    span = Span(**raw)
                    if span.trace_id != trace_id or not isinstance(span.span_id, str) or not span.span_id:
                        raise ValueError("invalid span identity")
                    for value in (span.parent_span_id, span.service_name):
                        if value is not None and not isinstance(value, str):
                            raise ValueError("parent/service must be strings or null")
                    if span.span_kind not in {"unknown", "internal", "client", "server", "producer", "consumer"}:
                        raise ValueError("invalid span kind")
                    if span.status not in {"unset", "ok", "error"}:
                        raise ValueError("invalid span status")
                    if not isinstance(span.operation_name, str) or not isinstance(span.attributes, dict):
                        raise ValueError("invalid operation/attributes")
                    for key, value in span.attributes.items():
                        if not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None))):
                            raise ValueError("attributes must map strings to scalar JSON values")
                        if isinstance(value, float) and not math.isfinite(value):
                            raise ValueError("attributes must be finite JSON values")
                    for value in (span.start_time_us, span.duration_us):
                        if value is not None and (type(value) is not int or value < 0):
                            raise ValueError("times must be nonnegative integer microseconds or null")
                    spans.append(span)
                traces.append(Trace(trace_id, tuple(spans)))
            return tuple(traces)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise SourceError(f"Cannot read normalized fixture: {exc}") from exc
