// Package httpio contains the small HTTP conventions shared by ShopSim services.
package httpio

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/telemetry"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric/noop"
	"go.opentelemetry.io/otel/trace"
)

func JSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		slog.Error("write response", "error", err)
	}
}

func Error(w http.ResponseWriter, status int, message string) {
	JSON(w, status, map[string]string{"error": message})
}

func Decode(w http.ResponseWriter, r *http.Request, value any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	err := decoder.Decode(value)
	if err == nil {
		var extra any
		err = decoder.Decode(&extra)
		if errors.Is(err, io.EOF) {
			return true
		}
	}
	slog.Warn("invalid JSON request", "path", r.URL.Path)
	Error(w, http.StatusBadRequest, "body must be a single valid JSON object with known fields (maximum 64 KiB)")
	return false
}

func Required(value string) bool { return strings.TrimSpace(value) != "" }

func Health(w http.ResponseWriter, r *http.Request) {
	JSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// Route keeps method and unknown-path errors consistent with the JSON API.
func Route(path, method string, handler http.HandlerFunc, readiness ...func(context.Context) error) http.Handler {
	// Only the application endpoint reaches this wrapper. Probes and routing
	// errors do not produce spans. The meter provider is explicitly a no-op.
	application := otelhttp.NewHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		trace.SpanFromContext(r.Context()).SetAttributes(attribute.String("http.route", path))
		handler(w, r)
	}), method+" "+path,
		otelhttp.WithSpanNameFormatter(func(operation string, _ *http.Request) string { return operation }),
		otelhttp.WithMeterProvider(noop.NewMeterProvider()))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		wanted := method
		isReady := r.URL.Path == "/readyz" && len(readiness) != 0
		if r.URL.Path == "/healthz" || isReady {
			wanted = http.MethodGet
		} else if r.URL.Path != path {
			Error(w, http.StatusNotFound, "not found")
			return
		}
		if r.Method != wanted {
			w.Header().Set("Allow", wanted)
			Error(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		if isReady {
			ctx, cancel := context.WithTimeout(r.Context(), time.Second)
			defer cancel()
			if err := readiness[0](ctx); err != nil {
				slog.Warn("readiness failed", "error", err)
				Error(w, http.StatusServiceUnavailable, "dependency unavailable")
				return
			}
			JSON(w, http.StatusOK, map[string]string{"status": "ready"})
			return
		}
		if r.URL.Path == "/healthz" {
			Health(w, r)
			return
		}
		application.ServeHTTP(w, r)
	})
}

func Run(service string, handler http.Handler) error {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	shutdown, err := telemetry.Init(context.Background(), service)
	if err != nil {
		slog.Warn("tracing initialization failed; continuing", "service", service, "error", err)
	}
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if err := shutdown(ctx); err != nil {
			slog.Warn("trace flush incomplete", "service", service, "error", err)
		}
	}()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	server := &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	slog.Info("service starting", "service", service, "address", addr)
	failed := make(chan error, 1)
	go func() { failed <- server.ListenAndServe() }()
	select {
	case err := <-failed:
		if !errors.Is(err, http.ErrServerClosed) {
			return err
		}
	case <-ctx.Done():
		drain, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(drain); err != nil {
			_ = server.Close()
			return err
		}
	}
	slog.Info("service stopped", "service", service)
	return nil
}
