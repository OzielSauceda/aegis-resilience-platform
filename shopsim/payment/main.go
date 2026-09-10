package main

import (
	"log/slog"
	"net/http"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/httpio"
)

type chargeRequest struct {
	OrderID     string `json:"order_id"`
	AmountCents int64  `json:"amount_cents"`
}

func handler() http.Handler {
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
		slog.Info("mock charge succeeded", "order_id", request.OrderID)
		httpio.JSON(w, http.StatusOK, map[string]string{"order_id": request.OrderID, "payment_id": "payment-" + request.OrderID, "status": "charged"})
	})
}

func main() { httpio.Run("payment", handler()) }
