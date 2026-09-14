package telemetry

import (
	"context"
	"errors"
	"strings"
	"testing"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func TestInitAndSafeErrors(t *testing.T) {
	previousProvider, previousPropagator := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	t.Cleanup(func() { otel.SetTracerProvider(previousProvider); otel.SetTextMapPropagator(previousPropagator) })
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
	t.Setenv("OTEL_SDK_DISABLED", "false")
	shutdown, err := Init(context.Background(), "inventory")
	if err != nil {
		t.Fatal(err)
	}
	provider := otel.GetTracerProvider().(*sdktrace.TracerProvider)
	exporter := tracetest.NewInMemoryExporter()
	provider.RegisterSpanProcessor(sdktrace.NewSimpleSpanProcessor(exporter))
	ctx, span := otel.Tracer("test").Start(context.Background(), "operation")
	if !span.SpanContext().IsSampled() {
		t.Fatal("development root not sampled")
	}
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	if carrier["traceparent"] == "" {
		t.Fatal("trace context not configured")
	}
	StorageError(span, "redis", errors.New("redis://user:secret@private-host:6379"))
	span.End()
	spans := exporter.GetSpans()
	if len(spans) != 1 || spans[0].Status.Code != codes.Error {
		t.Fatal("missing error span")
	}
	service := ""
	for _, attr := range spans[0].Resource.Attributes() {
		if attr.Key == "service.name" {
			service = attr.Value.AsString()
		}
	}
	if service != "inventory" {
		t.Fatalf("service.name=%s", service)
	}
	for _, event := range spans[0].Events {
		for _, attr := range event.Attributes {
			if strings.Contains(attr.Value.AsString(), "secret") || strings.Contains(attr.Value.AsString(), "private-host") {
				t.Fatal("driver details leaked")
			}
		}
	}
	if err := shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
}
