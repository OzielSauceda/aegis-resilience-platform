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
		{"valid", "POST", "/reserve", `{"order_id":"o1","sku":"s1","quantity":2}`, 200},
		{"invalid", "POST", "/reserve", `{"order_id":"o1","sku":"s1","quantity":0}`, 400},
		{"missing", "POST", "/reserve", "{}", 400},
		{"malformed", "POST", "/reserve", "{", 400},
		{"null", "POST", "/reserve", "null", 400},
		{"trailing", "POST", "/reserve", `{"order_id":"o1","sku":"s1","quantity":2} {}`, 400},
		{"unknown field", "POST", "/reserve", `{"extra":1}`, 400},
		{"wrong method", "GET", "/reserve", "", 405},
		{"not found", "GET", "/missing", "", 404},
		{"oversized", "POST", "/reserve", strings.Repeat(" ", 65537), 400},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler().ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body)))
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
			if tc.name == "valid" && (body["status"] != "reserved" || body["order_id"] != "o1" || body["reservation_id"] == "") {
				t.Fatalf("unexpected result: %v", body)
			}
			if tc.code >= http.StatusBadRequest && body["error"] == nil {
				t.Fatal("missing error")
			}
		})
	}
}
