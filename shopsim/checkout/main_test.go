package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

const validCheckout = `{"order_id":"o1","sku":"s1","quantity":2,"amount_cents":2500}`

func TestCheckout(t *testing.T) {
	for _, tc := range []struct {
		name                           string
		inventoryStatus, paymentStatus int
		inventoryBody, paymentBody     string
		slow, disconnected             bool
		want                           int
		wantPayment                    bool
	}{
		{name: "success", want: 200, wantPayment: true},
		{name: "inventory failure", inventoryStatus: 503, want: 502},
		{name: "payment failure", paymentStatus: 500, want: 502, wantPayment: true},
		{name: "inventory conflict", inventoryStatus: 409, want: 409},
		{name: "payment conflict", paymentStatus: 409, want: 409, wantPayment: true},
		{name: "inventory malformed", inventoryBody: "{", want: 502},
		{name: "inventory empty", inventoryBody: "{}", want: 502},
		{name: "payment malformed", paymentBody: "{", want: 502, wantPayment: true},
		{name: "payment mismatch", paymentBody: `{"order_id":"other","status":"charged","payment_id":"p"}`, want: 502, wantPayment: true},
		{name: "timeout", slow: true, want: 504},
		{name: "connection failure", disconnected: true, want: 502},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Events are emitted by real HTTP handlers. Their order verifies sequencing.
			events := make(chan string, 2)
			inventory := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				events <- "inventory"
				if r.Method != "POST" || r.URL.Path != "/reserve" {
					t.Error("incorrect inventory route")
				}
				var body struct {
					OrderID  string `json:"order_id"`
					SKU      string `json:"sku"`
					Quantity int    `json:"quantity"`
				}
				decoder := json.NewDecoder(r.Body)
				decoder.DisallowUnknownFields()
				if err := decoder.Decode(&body); err != nil || body.OrderID != "o1" || body.SKU != "s1" || body.Quantity != 2 {
					t.Errorf("inventory payload: %+v, %v", body, err)
				}
				if tc.slow {
					<-r.Context().Done()
					return
				}
				if tc.inventoryStatus != 0 {
					w.WriteHeader(tc.inventoryStatus)
					return
				}
				if tc.inventoryBody != "" {
					_, _ = w.Write([]byte(tc.inventoryBody))
					return
				}
				_, _ = w.Write([]byte(`{"order_id":"o1","status":"reserved","reservation_id":"reservation-o1"}`))
			}))
			defer inventory.Close()
			payment := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				events <- "payment"
				if r.Method != "POST" || r.URL.Path != "/charge" {
					t.Error("incorrect payment route")
				}
				var body struct {
					OrderID     string `json:"order_id"`
					AmountCents int64  `json:"amount_cents"`
				}
				decoder := json.NewDecoder(r.Body)
				decoder.DisallowUnknownFields()
				if err := decoder.Decode(&body); err != nil || body.OrderID != "o1" || body.AmountCents != 2500 {
					t.Errorf("payment payload: %+v, %v", body, err)
				}
				if tc.paymentStatus != 0 {
					w.WriteHeader(tc.paymentStatus)
					return
				}
				if tc.paymentBody != "" {
					_, _ = w.Write([]byte(tc.paymentBody))
					return
				}
				_, _ = w.Write([]byte(`{"order_id":"o1","status":"charged","payment_id":"payment-o1"}`))
			}))
			defer payment.Close()
			if tc.disconnected {
				inventory.Close()
			}
			client := &http.Client{Timeout: time.Second}
			if tc.slow {
				client.Timeout = 100 * time.Millisecond
			}
			c := checkout{client: client, inventoryURL: inventory.URL, paymentURL: payment.URL}
			recorder := httptest.NewRecorder()
			c.handler().ServeHTTP(recorder, httptest.NewRequest("POST", "/checkout", strings.NewReader(validCheckout)))
			if recorder.Code != tc.want {
				t.Fatalf("status = %d, want %d: %s", recorder.Code, tc.want, recorder.Body)
			}
			var body struct {
				Status    string `json:"status"`
				Inventory result `json:"inventory"`
				Payment   result `json:"payment"`
				Error     string `json:"error"`
			}
			if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if tc.want == 200 && (body.Status != "completed" || body.Inventory.ReservationID != "reservation-o1" || body.Payment.PaymentID != "payment-o1") {
				t.Fatalf("unexpected success: %+v", body)
			}
			if tc.want != 200 && body.Error == "" {
				t.Fatal("missing JSON error")
			}
			if !tc.disconnected {
				if event := <-events; event != "inventory" {
					t.Fatalf("first call: %s", event)
				}
			}
			if tc.wantPayment {
				select {
				case event := <-events:
					if event != "payment" {
						t.Fatal(event)
					}
				default:
					t.Fatal("payment not called")
				}
			} else if len(events) != 0 {
				t.Fatal("payment called after inventory failed")
			}
		})
	}
}

func TestCheckoutValidation(t *testing.T) {
	// No client is supplied: invalid requests and health must never call downstreams.
	c := checkout{}
	for _, tc := range []struct {
		method, path, body string
		want               int
	}{
		{"GET", "/healthz", "", 200},
		{"POST", "/checkout", "{", 400},
		{"POST", "/checkout", "{}", 400},
		{"POST", "/checkout", "null", 400},
		{"POST", "/checkout", validCheckout + "{}", 400},
		{"POST", "/checkout", strings.Replace(validCheckout, `"quantity":2`, `"quantity":-1`, 1), 400},
		{"POST", "/checkout", strings.Replace(validCheckout, `"amount_cents":2500`, `"amount_cents":0`, 1), 400},
		{"POST", "/checkout", strings.Replace(validCheckout, `"sku":"s1"`, `"sku":" "`, 1), 400},
		{"GET", "/checkout", "", 405},
		{"GET", "/missing", "", 404},
	} {
		r := httptest.NewRecorder()
		c.handler().ServeHTTP(r, httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body)))
		if r.Code != tc.want {
			t.Errorf("%s %s %s: status %d, want %d", tc.method, tc.path, tc.body, r.Code, tc.want)
		}
	}
}

func TestServiceURL(t *testing.T) {
	for _, value := range []string{"", "localhost:8080", "ftp://host", "http://user:password@host", "http://host/path", "http://host?query=1"} {
		t.Setenv("TEST_SERVICE_URL", value)
		if _, err := serviceURL("TEST_SERVICE_URL"); err == nil {
			t.Errorf("accepted %q", value)
		}
	}
	t.Setenv("TEST_SERVICE_URL", "http://inventory:8080/")
	if got, err := serviceURL("TEST_SERVICE_URL"); err != nil || got != "http://inventory:8080" {
		t.Fatalf("%s, %v", got, err)
	}
}
