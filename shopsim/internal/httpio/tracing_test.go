package httpio

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

func TestProbesAndHTTPStatusTracing(t *testing.T) {
	previousProvider, previousPropagator := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter), sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.AlwaysSample())))
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	t.Cleanup(func() {
		_ = provider.Shutdown(context.Background())
		otel.SetTracerProvider(previousProvider)
		otel.SetTextMapPropagator(previousPropagator)
	})
	status := 200
	handler := Route("/operation", "POST", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status) }, func(context.Context) error { return nil })
	for _, path := range []string{"/healthz", "/readyz"} {
		for i := 0; i < 10; i++ {
			handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", path, nil))
		}
	}
	if len(exporter.GetSpans()) != 0 {
		t.Fatal("probes generated spans")
	}
	for _, code := range []int{200, 409, 503} {
		status = code
		handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/operation", nil))
	}
	spans := exporter.GetSpans()
	if len(spans) != 3 {
		t.Fatalf("spans=%d", len(spans))
	}
	if spans[1].Status.Code == codes.Error || spans[2].Status.Code != codes.Error {
		t.Fatal("business conflict/infrastructure failure status incorrect")
	}
	// A standard incoming parent is preserved without inventing trace IDs.
	ctx, parent := provider.Tracer("test").Start(context.Background(), "caller")
	r := httptest.NewRequest(http.MethodPost, "/operation", nil)
	propagation.TraceContext{}.Inject(ctx, propagation.HeaderCarrier(r.Header))
	handler.ServeHTTP(httptest.NewRecorder(), r)
	span := exporter.GetSpans()[3]
	if span.Parent.SpanID() != parent.SpanContext().SpanID() || span.SpanContext.TraceID() != parent.SpanContext().TraceID() || span.SpanKind != trace.SpanKindServer {
		t.Fatal("incoming W3C parent not preserved")
	}
	parent.End()
}
