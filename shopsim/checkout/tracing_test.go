package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/httpio"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

type countingTransport struct {
	base  http.RoundTripper
	calls atomic.Int32
}

func (c *countingTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	c.calls.Add(1)
	return c.base.RoundTrip(r)
}

func TestDistributedHTTPTracing(t *testing.T) {
	previousProvider, previousPropagator := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	t.Cleanup(func() {
		_ = provider.Shutdown(context.Background())
		otel.SetTracerProvider(previousProvider)
		otel.SetTextMapPropagator(previousPropagator)
	})

	downstream := func(path, response string) *httptest.Server {
		return httptest.NewServer(httpio.Route(path, "POST", func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("traceparent") == "" {
				t.Error("missing propagated traceparent")
			}
			if !trace.SpanFromContext(r.Context()).SpanContext().IsValid() {
				t.Error("missing server context")
			}
			_, _ = w.Write([]byte(response))
		}))
	}
	inventory := downstream("/reserve", `{"order_id":"o1","status":"reserved","reservation_id":"reservation-o1"}`)
	defer inventory.Close()
	payment := downstream("/charge", `{"order_id":"o1","status":"charged","payment_id":"payment-o1"}`)
	defer payment.Close()
	base := &countingTransport{base: http.DefaultTransport}
	c := checkout{client: newHTTPClient(base), inventoryURL: inventory.URL, paymentURL: payment.URL}
	if c.client.Timeout != 2*time.Second {
		t.Fatal("HTTP timeout changed")
	}
	server := httptest.NewServer(c.handler())
	defer server.Close()
	response, err := http.Post(server.URL+"/checkout", "application/json", strings.NewReader(validCheckout))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	response.Body.Close()
	if response.StatusCode != 200 || base.calls.Load() != 2 {
		t.Fatalf("status=%d wrapped transport calls=%d", response.StatusCode, base.calls.Load())
	}
	// The server can finish ending its span just after the client receives EOF.
	deadline := time.Now().Add(time.Second)
	for len(exporter.GetSpans()) < 5 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	spans := exporter.GetSpans()
	if len(spans) != 5 {
		t.Fatalf("spans=%d, want 3 servers + 2 clients", len(spans))
	}
	byID := make(map[trace.SpanID]tracetest.SpanStub)
	var root tracetest.SpanStub
	for _, span := range spans {
		byID[span.SpanContext.SpanID()] = span
		if span.Name == "POST /checkout" {
			root = span
		}
	}
	if root.SpanKind != trace.SpanKindServer || root.Parent.IsValid() {
		t.Fatal("invalid root SERVER span")
	}
	for _, span := range spans {
		if span.SpanContext.TraceID() != root.SpanContext.TraceID() {
			t.Fatal("split trace IDs")
		}
		if span.EndTime.Before(span.StartTime) {
			t.Fatal("negative span duration")
		}
		if span.SpanContext.SpanID() == root.SpanContext.SpanID() {
			continue
		}
		parent := byID[span.Parent.SpanID()]
		if span.SpanKind == trace.SpanKindClient && parent.SpanContext.SpanID() != root.SpanContext.SpanID() {
			t.Fatal("client not child of checkout")
		}
		if span.SpanKind == trace.SpanKindServer && parent.SpanKind != trace.SpanKindClient {
			t.Fatal("downstream server not child of client")
		}
	}
	if c.client.CheckRedirect(nil, nil) != http.ErrUseLastResponse {
		t.Fatal("redirect policy changed")
	}
}
