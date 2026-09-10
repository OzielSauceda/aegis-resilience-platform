package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHTTP(t *testing.T) {
	for _, tc := range []struct {
		name, method, path, body string
		code                     int
	}{
		{"health", "GET", "/healthz", "", 200},
		{"valid", "POST", "/charge", `{"order_id":"o1","amount_cents":2500}`, 200},
		{"invalid", "POST", "/charge", `{"order_id":"o1","amount_cents":-1}`, 400},
		{"missing", "POST", "/charge", "{}", 400},
		{"malformed", "POST", "/charge", "{", 400},
		{"null", "POST", "/charge", "null", 400},
		{"trailing", "POST", "/charge", `{"order_id":"o1","amount_cents":2500} {}`, 400},
		{"unknown field", "POST", "/charge", `{"extra":1}`, 400},
		{"wrong method", "GET", "/charge", "", 405},
		{"not found", "GET", "/missing", "", 404},
		{"oversized", "POST", "/charge", strings.Repeat(" ", 65537), 400},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler(&stubStore{}).ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body)))
			if recorder.Code != tc.code {
				t.Fatalf("status = %d, want %d: %s", recorder.Code, tc.code, recorder.Body)
			}
			if recorder.Header().Get("Content-Type") != "application/json" {
				t.Fatal("expected JSON")
			}
			var body map[string]any
			if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if tc.name == "valid" && (body["status"] != "charged" || body["order_id"] != "o1" || body["payment_id"] == "") {
				t.Fatalf("unexpected result: %v", body)
			}
			if tc.code >= http.StatusBadRequest && body["error"] == nil {
				t.Fatal("missing error")
			}
		})
	}
}
