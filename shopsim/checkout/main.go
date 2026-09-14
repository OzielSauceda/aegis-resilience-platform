package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/httpio"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric/noop"
	"go.opentelemetry.io/otel/trace"
)

type checkoutRequest struct {
	OrderID     string `json:"order_id"`
	SKU         string `json:"sku"`
	Quantity    int    `json:"quantity"`
	AmountCents int64  `json:"amount_cents"`
}

type result struct {
	OrderID       string `json:"order_id"`
	Status        string `json:"status"`
	ReservationID string `json:"reservation_id,omitempty"`
	PaymentID     string `json:"payment_id,omitempty"`
}

type checkout struct {
	client       *http.Client
	inventoryURL string
	paymentURL   string
}

type downstreamStatus int

func (s downstreamStatus) Error() string { return fmt.Sprintf("downstream HTTP status %d", s) }

func newHTTPClient(base http.RoundTripper) *http.Client {
	return &http.Client{
		Timeout:       2 * time.Second,
		Transport:     otelhttp.NewTransport(base, otelhttp.WithMeterProvider(noop.NewMeterProvider())),
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

func (c checkout) call(ctx context.Context, endpoint string, payload any) (result, error) {
	var output result
	body, err := json.Marshal(payload)
	if err != nil {
		return output, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return output, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := c.client.Do(request)
	if err != nil {
		return output, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return output, downstreamStatus(response.StatusCode)
	}
	body, err = io.ReadAll(io.LimitReader(response.Body, (64<<10)+1))
	if err != nil {
		return output, err
	}
	if len(body) > 64<<10 {
		return output, errors.New("downstream response too large")
	}
	err = json.Unmarshal(body, &output)
	return output, err
}

func downstreamError(w http.ResponseWriter, service, orderID string, err error) {
	slog.Error("downstream request failed", "service", service, "order_id", orderID, "error", err)
	status := http.StatusBadGateway
	var downstream downstreamStatus
	if errors.As(err, &downstream) && downstream == http.StatusConflict {
		status = http.StatusConflict
	}
	var timeout net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &timeout) && timeout.Timeout()) {
		status = http.StatusGatewayTimeout
	}
	httpio.Error(w, status, service+" request failed")
}

func (c checkout) handler() http.Handler {
	return httpio.Route("/checkout", http.MethodPost, func(w http.ResponseWriter, r *http.Request) {
		var request checkoutRequest
		if !httpio.Decode(w, r, &request) {
			return
		}
		if !httpio.Required(request.OrderID) || !httpio.Required(request.SKU) || request.Quantity <= 0 || request.AmountCents <= 0 {
			slog.Warn("invalid checkout")
			httpio.Error(w, http.StatusBadRequest, "order_id, sku, positive quantity and positive amount_cents are required")
			return
		}
		span := trace.SpanFromContext(r.Context())
		span.SetAttributes(attribute.String("shopsim.order_id", request.OrderID), attribute.String("shopsim.sku", request.SKU),
			attribute.Int("shopsim.quantity", request.Quantity), attribute.Int64("shopsim.amount_cents", request.AmountCents))
		reservation, err := c.call(r.Context(), c.inventoryURL+"/reserve", map[string]any{"order_id": request.OrderID, "sku": request.SKU, "quantity": request.Quantity})
		if err == nil && (reservation.OrderID != request.OrderID || reservation.Status != "reserved" || !httpio.Required(reservation.ReservationID)) {
			err = errors.New("invalid reservation response")
		}
		if err != nil {
			downstreamError(w, "inventory", request.OrderID, err)
			return
		}
		payment, err := c.call(r.Context(), c.paymentURL+"/charge", map[string]any{"order_id": request.OrderID, "amount_cents": request.AmountCents})
		if err == nil && (payment.OrderID != request.OrderID || payment.Status != "charged" || !httpio.Required(payment.PaymentID)) {
			err = errors.New("invalid payment response")
		}
		if err != nil {
			slog.Warn("partial checkout failure: reservation remains", "order_id", request.OrderID, "reservation_id", reservation.ReservationID)
			span.AddEvent("reservation retained after payment failure")
			downstreamError(w, "payment", request.OrderID, err)
			return
		}
		slog.Info("checkout succeeded", "order_id", request.OrderID)
		httpio.JSON(w, http.StatusOK, map[string]any{"order_id": request.OrderID, "status": "completed", "inventory": reservation, "payment": payment})
	})
}

func serviceURL(key string) (string, error) {
	value := strings.TrimRight(os.Getenv(key), "/")
	parsed, err := url.Parse(value)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path != "" {
		return "", fmt.Errorf("%s must be an HTTP(S) origin such as http://localhost:8081", key)
	}
	return value, nil
}

func main() {
	inventory, err := serviceURL("INVENTORY_URL")
	if err != nil {
		slog.Error("invalid configuration", "error", err)
		os.Exit(1)
	}
	payment, err := serviceURL("PAYMENT_URL")
	if err != nil {
		slog.Error("invalid configuration", "error", err)
		os.Exit(1)
	}
	c := checkout{client: newHTTPClient(nil), inventoryURL: inventory, paymentURL: payment}
	if err := httpio.Run("checkout", c.handler()); err != nil {
		slog.Error("server failed", "error", err)
		os.Exit(1)
	}
}
