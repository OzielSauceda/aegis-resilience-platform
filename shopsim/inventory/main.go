package main

import (
	"log/slog"
	"net/http"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/httpio"
)

type reserveRequest struct {
	OrderID  string `json:"order_id"`
	SKU      string `json:"sku"`
	Quantity int    `json:"quantity"`
}

func handler() http.Handler {
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
		slog.Info("mock reservation succeeded", "order_id", request.OrderID)
		httpio.JSON(w, http.StatusOK, map[string]string{"order_id": request.OrderID, "reservation_id": "reservation-" + request.OrderID, "status": "reserved"})
	})
}

func main() { httpio.Run("inventory", handler()) }
