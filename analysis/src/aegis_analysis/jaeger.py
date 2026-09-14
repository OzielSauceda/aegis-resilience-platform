"""Adapter for the pinned Jaeger v2 query JSON used by Batch 3 validation."""

import json
import math
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import urlopen

from .model import Diagnostic, Span, Trace
from .sources import SourceError


def _identity(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def normalize_jaeger(payload: object) -> tuple[Trace, ...]:
    """Accept an API envelope or a single exported Batch 3 trace."""
    if not isinstance(payload, dict):
        raise SourceError("Jaeger response must be an object")
    if payload.get("errors"):
        raise SourceError("Jaeger returned query errors")
    records = payload.get("data") if "data" in payload else [payload]
    if not isinstance(records, list):
        raise SourceError("Jaeger data must be a list")
    traces = []
    for record in records:
        if not isinstance(record, dict) or not _identity(record.get("traceID")):
            raise SourceError("Jaeger trace has no usable traceID")
        trace_id = record["traceID"]
        if not isinstance(record.get("spans"), list):
            raise SourceError("Jaeger trace spans must be a list")
        processes = record.get("processes") or {}
        if not isinstance(processes, dict):
            raise SourceError("Jaeger processes must be an object")
        spans = []
        diagnostics = set()
        for raw in record["spans"]:
            if not isinstance(raw, dict):
                diagnostics.add(Diagnostic("invalid_span", trace_id))
                continue
            span_id = _identity(raw.get("spanID"))
            if span_id is None or raw.get("traceID") != trace_id:
                diagnostics.add(Diagnostic("invalid_identity", trace_id, span_id or ""))
                continue
            def warn(code: str) -> None:
                diagnostics.add(Diagnostic(code, trace_id, span_id))

            attributes = {}
            rejected_tags = set()
            tags = raw.get("tags") or []
            if not isinstance(tags, list):
                warn("invalid_tags")
                tags = []
            for tag in tags:
                if not isinstance(tag, dict) or not isinstance(tag.get("key"), str):
                    warn("invalid_tags")
                    continue
                key, value = tag["key"], tag.get("value")
                if not isinstance(value, (str, int, float, bool, type(None))) or (isinstance(value, float) and not math.isfinite(value)):
                    warn("invalid_tags")
                    rejected_tags.add(key)
                elif key in attributes and (type(attributes[key]) is not type(value) or attributes[key] != value):
                    warn("invalid_tags")
                    rejected_tags.add(key)
                else:
                    attributes[key] = value
            for key in rejected_tags:
                attributes.pop(key, None)
            kind = attributes.get("span.kind", "unknown")
            if kind not in ("server", "client", "internal", "producer", "consumer"):
                kind = "unknown"
            status_tag = attributes.get("otel.status_code")
            status = "unset"
            if attributes.get("error") is True or status_tag in ("ERROR", "error", 2):
                status = "error"
            elif status_tag in ("OK", "ok", 1):
                status = "ok"

            parents = set()
            invalid_parent = False
            references = raw.get("references") or []
            if not isinstance(references, list):
                references = []
                invalid_parent = True
            for ref in references:
                if not isinstance(ref, dict):
                    invalid_parent = True
                elif ref.get("refType") == "CHILD_OF":
                    parent_id = _identity(ref.get("spanID"))
                    if ref.get("traceID") != trace_id or parent_id is None:
                        invalid_parent = True
                    else:
                        parents.add(parent_id)
                else:
                    warn("unsupported_reference")
            if invalid_parent or len(parents) > 1:
                warn("ambiguous_parent")
                parent_id = None
            else:
                parent_id = next(iter(parents), None)

            process_id = raw.get("processID")
            process = processes.get(process_id, {}) if isinstance(process_id, str) else {}
            service = _identity(process.get("serviceName")) if isinstance(process, dict) else None
            def timestamp(name: str) -> int | None:
                value = raw.get(name)
                if type(value) is int and value >= 0:
                    return value
                warn("invalid_timing")
                return None

            operation = raw.get("operationName", "")
            if not isinstance(operation, str):
                operation = ""
                warn("invalid_operation")
            spans.append(Span(trace_id, span_id, parent_id, service, operation, kind,
                              timestamp("startTime"), timestamp("duration"), status, attributes))
        traces.append(Trace(trace_id, tuple(spans), tuple(sorted(diagnostics))))
    return tuple(traces)


class JaegerFileSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read_traces(self) -> tuple[Trace, ...]:
        try:
            return normalize_jaeger(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as exc:
            raise SourceError(f"Cannot read Jaeger file: {exc}") from exc


class JaegerSource:
    """One bounded query, with either an exact trace ID or a service filter."""

    def __init__(self, url: str = "http://127.0.0.1:16686", *, service: str | None = None,
                 trace_id: str | None = None, lookback: str = "1h", limit: int = 20,
                 tags: dict[str, str] | None = None, timeout: float = 5):
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise SourceError(f"Invalid Jaeger URL: {exc}") from exc
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
            raise SourceError("Jaeger URL must be an HTTP(S) base URL")
        if bool(service) == bool(trace_id):
            raise SourceError("Choose exactly one Jaeger service or trace ID")
        if limit <= 0 or not 0 < timeout < float("inf"):
            raise SourceError("Query limit and timeout must be positive and finite")
        self.timeout = timeout
        if trace_id:
            self.url = url.rstrip("/") + "/api/traces/" + quote(trace_id, safe="")
        else:
            query = {"service": service, "lookback": lookback, "limit": limit}
            if tags:
                query["tags"] = json.dumps(tags, sort_keys=True)
            self.url = url.rstrip("/") + "/api/traces?" + urlencode(query)

    def read_traces(self) -> tuple[Trace, ...]:
        try:
            with urlopen(self.url, timeout=self.timeout) as response:
                return normalize_jaeger(json.load(response))
        except (URLError, OSError, ValueError) as exc:
            raise SourceError(f"Cannot read Jaeger traces: {exc}") from exc
