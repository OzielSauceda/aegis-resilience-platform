package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"
	"time"
)

// Uses unique order IDs and deletes only its own rows; never resets the ledger.
func TestPostgresLedger(t *testing.T) {
	connection := os.Getenv("TEST_DATABASE_URL")
	if connection == "" {
		t.Skip("set TEST_DATABASE_URL to run real PostgreSQL tests")
	}
	store, err := openPostgres(connection)
	if err != nil {
		t.Fatal(err)
	}
	defer store.db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	orderID := fmt.Sprintf("test-ledger-%d", time.Now().UnixNano())
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), 3*time.Second)
		defer stop()
		if _, err := store.db.ExecContext(cleanup, "DELETE FROM payments WHERE order_id = $1", orderID); err != nil {
			t.Error(err)
		}
	}()
	request := chargeRequest{OrderID: orderID, AmountCents: 2500}
	const count = 12
	var wg sync.WaitGroup
	replays := make(chan bool, count)
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			replay, err := store.Charge(ctx, request)
			if err != nil {
				t.Error(err)
				return
			}
			replays <- replay
		}()
	}
	wg.Wait()
	close(replays)
	created, replayed := 0, 0
	for replay := range replays {
		if replay {
			replayed++
		} else {
			created++
		}
	}
	if created != 1 || replayed != count-1 {
		t.Fatalf("created=%d replayed=%d", created, replayed)
	}
	var before time.Time
	if err := store.db.QueryRowContext(ctx, "SELECT created_at FROM payments WHERE order_id=$1", orderID).Scan(&before); err != nil {
		t.Fatal(err)
	}
	if replay, err := store.Charge(ctx, request); err != nil || !replay {
		t.Fatalf("replay=%v err=%v", replay, err)
	}
	request.AmountCents++
	if _, err := store.Charge(ctx, request); !errors.Is(err, errConflict) {
		t.Fatalf("conflict: %v", err)
	}
	var rows int
	var amount int64
	var status string
	var after time.Time
	if err := store.db.QueryRowContext(ctx, "SELECT count(*), min(amount_cents), min(status), min(created_at) FROM payments WHERE order_id=$1", orderID).Scan(&rows, &amount, &status, &after); err != nil {
		t.Fatal(err)
	}
	if rows != 1 || amount != 2500 || status != "charged" || !before.Equal(after) {
		t.Fatalf("ledger changed: %d %d %s %v", rows, amount, status, after)
	}
	if err := store.Ping(ctx); err != nil {
		t.Fatal(err)
	}
	canceled, stop := context.WithCancel(ctx)
	stop()
	if _, err := store.Charge(canceled, request); err == nil {
		t.Fatal("canceled operation succeeded")
	}
}
