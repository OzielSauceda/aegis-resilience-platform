package main

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
)

type stubStore struct {
	replayed bool
	err      error
	calls    int
}

func (s *stubStore) Charge(ctx context.Context, request chargeRequest) (bool, error) {
	s.calls++
	if _, ok := ctx.Deadline(); !ok {
		return false, errors.New("missing operation deadline")
	}
	return s.replayed, s.err
}
func (s *stubStore) Ping(ctx context.Context) error { return s.err }

func TestStorageHTTP(t *testing.T) {
	var success string
	for _, tc := range []struct {
		name  string
		store *stubStore
		want  int
	}{
		{"created", &stubStore{}, 200},
		{"replay", &stubStore{replayed: true}, 200},
		{"conflict", &stubStore{err: errConflict}, 409},
		{"storage failure", &stubStore{err: errors.New("private connection details")}, 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler(tc.store).ServeHTTP(recorder, httptest.NewRequest("POST", "/charge", strings.NewReader(`{"order_id":"o1","amount_cents":2500}`)))
			if recorder.Code != tc.want {
				t.Fatalf("status %d, want %d: %s", recorder.Code, tc.want, recorder.Body)
			}
			if tc.store.calls != 1 {
				t.Fatalf("store calls = %d", tc.store.calls)
			}
			if strings.Contains(recorder.Body.String(), "private") {
				t.Fatal("storage error leaked")
			}
			if tc.name == "created" {
				success = recorder.Body.String()
			}
			if tc.name == "replay" && recorder.Body.String() != success {
				t.Fatal("replay changed response")
			}
		})
	}
}

func TestReadinessAndValidation(t *testing.T) {
	for _, unavailable := range []bool{false, true} {
		store := &stubStore{}
		if unavailable {
			store.err = errors.New("storage down")
		}
		for _, tc := range []struct {
			method, path, body string
			want               int
		}{
			{"GET", "/healthz", "", 200},
			{"GET", "/readyz", "", map[bool]int{false: 200, true: 503}[unavailable]},
			{"POST", "/readyz", "", 405},
			{"POST", "/charge", "{}", 400},
		} {
			recorder := httptest.NewRecorder()
			handler(store).ServeHTTP(recorder, httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body)))
			if recorder.Code != tc.want {
				t.Errorf("%s %s: %d, want %d", tc.method, tc.path, recorder.Code, tc.want)
			}
		}
		if store.calls != 0 {
			t.Fatal("validation or probes called storage mutation")
		}
	}
}
