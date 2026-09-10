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

type chargeRequest struct {
	OrderID     string `json:"order_id"`
	AmountCents int64  `json:"amount_cents"`
}

func handler(store paymentStore) http.Handler {
	return httpio.Route("/charge", http.MethodPost, func(w http.ResponseWriter, r *http.Request) {
		var request chargeRequest
		if !httpio.Decode(w, r, &request) {
			return
		}
		if !httpio.Required(request.OrderID) || request.AmountCents <= 0 {
			slog.Warn("invalid charge")
			httpio.Error(w, http.StatusBadRequest, "order_id and positive amount_cents are required")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), time.Second)
		defer cancel()
		replayed, err := store.Charge(ctx, request)
		if errors.Is(err, errConflict) {
			slog.Warn("payment conflict", "service", "payment", "order_id", request.OrderID)
			httpio.Error(w, http.StatusConflict, errConflict.Error())
			return
		}
		if err != nil {
			slog.Error("PostgreSQL charge failed", "service", "payment", "order_id", request.OrderID, "error", err)
			httpio.Error(w, http.StatusServiceUnavailable, "payment storage unavailable")
			return
		}
		message := "payment created"
		if replayed {
			message = "idempotent payment replay"
		}
		slog.Info(message, "service", "payment", "order_id", request.OrderID)
		httpio.JSON(w, http.StatusOK, map[string]string{"order_id": request.OrderID, "payment_id": "payment-" + request.OrderID, "status": "charged"})
	}, store.Ping)
}

func main() {
	connection := os.Getenv("DATABASE_URL")
	if !httpio.Required(connection) {
		slog.Error("DATABASE_URL is required")
		os.Exit(1)
	}
	store, err := openPostgres(connection)
	if err != nil {
		slog.Error("invalid PostgreSQL configuration")
		os.Exit(1)
	}
	defer store.db.Close()
	httpio.Run("payment", handler(store))
}
