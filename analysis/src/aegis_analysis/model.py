"""Source-independent trace and graph contracts. Times are integer microseconds."""

from dataclasses import asdict, dataclass, field
import json
from typing import Literal, Mapping

Attribute = str | int | float | bool | None
SpanKind = Literal["server", "client", "producer", "consumer", "internal", "unknown"]
Status = Literal["unset", "ok", "error"]


@dataclass(frozen=True, order=True)
class Diagnostic:
    code: str
    trace_id: str
    span_id: str = ""


@dataclass(frozen=True)
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service_name: str | None
    operation_name: str = ""
    span_kind: SpanKind = "unknown"
    start_time_us: int | None = None
    duration_us: int | None = None
    status: Status = "unset"
    attributes: Mapping[str, Attribute] = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    trace_id: str
    spans: tuple[Span, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    request_count: int
    error_count: int


@dataclass(frozen=True)
class ServiceGraph:
    nodes: tuple[str, ...]
    edges: tuple[Edge, ...]
    diagnostics: tuple[Diagnostic, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "nodes": [{"service": service} for service in sorted(self.nodes)],
            "edges": [asdict(edge) for edge in sorted(self.edges)],
            "diagnostics": [asdict(item) for item in sorted(set(self.diagnostics))],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
