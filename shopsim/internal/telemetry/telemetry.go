// Package telemetry initializes ShopSim's tracing-only OpenTelemetry SDK.
package telemetry

import (
	"context"
	"errors"
	"os"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
	"go.opentelemetry.io/otel/trace/noop"
)

// Init does not wait for a Collector connection. With no endpoint, local runs
// retain context propagation but do not start an exporter. Tests need no backend.
func Init(ctx context.Context, service string) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	if os.Getenv("OTEL_SDK_DISABLED") == "true" {
		otel.SetTracerProvider(noop.NewTracerProvider())
		return func(context.Context) error { return nil }, nil
	}
	options := []sdktrace.TracerProviderOption{
		sdktrace.WithResource(resource.NewSchemaless(attribute.String("service.name", service), attribute.String("service.namespace", "shopsim"))),
		sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.AlwaysSample())),
	}
	if os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") != "" || os.Getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") != "" {
		exporter, err := otlptracegrpc.New(ctx, otlptracegrpc.WithTimeout(2*time.Second))
		if err != nil {
			otel.SetTracerProvider(noop.NewTracerProvider())
			return func(context.Context) error { return nil }, errors.New("trace exporter initialization failed; tracing disabled")
		}
		// Never block business requests on a full queue. The SDK drops excess
		// spans; network/export retries happen only in the background.
		options = append(options, sdktrace.WithBatcher(exporter,
			sdktrace.WithMaxQueueSize(512), sdktrace.WithMaxExportBatchSize(128),
			sdktrace.WithBatchTimeout(time.Second), sdktrace.WithExportTimeout(2*time.Second)))
	}
	provider := sdktrace.NewTracerProvider(options...)
	otel.SetTracerProvider(provider)
	return provider.Shutdown, nil
}

// StorageError records bounded, credential-free evidence. Driver error strings
// can contain connection details, so the raw error belongs in application logs.
func StorageError(span trace.Span, system string, err error) {
	kind := "unavailable"
	if errors.Is(err, context.DeadlineExceeded) {
		kind = "timeout"
	}
	if errors.Is(err, context.Canceled) {
		kind = "canceled"
	}
	message := system + " operation " + kind
	span.SetAttributes(attribute.String("error.type", kind))
	span.RecordError(errors.New(message))
	span.SetStatus(codes.Error, message)
}
