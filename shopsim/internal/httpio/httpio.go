// Package httpio contains the small HTTP conventions shared by ShopSim services.
package httpio

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"
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
func Route(path, method string, handler http.HandlerFunc) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		wanted := method
		if r.URL.Path == "/healthz" {
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
		if r.URL.Path == "/healthz" {
			Health(w, r)
			return
		}
		handler(w, r)
	})
}

func Run(service string, handler http.Handler) {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	server := &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	slog.Info("service starting", "service", service, "address", addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("server failed", "service", service, "error", err)
		os.Exit(1)
	}
}
