package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/httpio"
)

type reserveRequest struct {
	OrderID  string `json:"order_id"`
	SKU      string `json:"sku"`
	Quantity int    `json:"quantity"`
}

func handler(store inventoryStore) http.Handler {
	return httpio.Route("/reserve", http.MethodPost, func(w http.ResponseWriter, r *http.Request) {
		var request reserveRequest
		if !httpio.Decode(w, r, &request) {
			return
		}
		if !httpio.Required(request.OrderID) || !httpio.Required(request.SKU) || request.Quantity <= 0 {
			slog.Warn("invalid reservation")
			httpio.Error(w, http.StatusBadRequest, "order_id, sku and positive quantity are required")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), time.Second)
		defer cancel()
		replayed, err := store.Reserve(ctx, request)
		if err != nil {
			status := http.StatusServiceUnavailable
			message := "inventory storage unavailable"
			switch {
			case errors.Is(err, errConflict), errors.Is(err, errInsufficientStock):
				status, message = http.StatusConflict, err.Error()
			case errors.Is(err, errUnknownSKU):
				status, message = http.StatusNotFound, err.Error()
			}
			slog.Warn("reservation failed", "service", "inventory", "order_id", request.OrderID, "sku", request.SKU, "quantity", request.Quantity, "error", err)
			httpio.Error(w, status, message)
			return
		}
		message := "reservation created"
		if replayed {
			message = "idempotent reservation replay"
		}
		slog.Info(message, "service", "inventory", "order_id", request.OrderID, "sku", request.SKU, "quantity", request.Quantity)
		httpio.JSON(w, http.StatusOK, map[string]string{"order_id": request.OrderID, "reservation_id": "reservation-" + request.OrderID, "status": "reserved"})
	}, store.Ping)
}

func main() {
	connection := os.Getenv("REDIS_URL")
	if !httpio.Required(connection) {
		slog.Error("REDIS_URL is required")
		os.Exit(1)
	}
	store, err := openRedis(connection)
	if err != nil {
		slog.Error("invalid Redis configuration")
		os.Exit(1)
	}
	defer store.client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	err = store.Seed(ctx)
	cancel()
	if err != nil {
		slog.Error("Redis initialization failed", "error", err)
		os.Exit(1)
	}
	if err := httpio.Run("inventory", handler(store)); err != nil {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
}
