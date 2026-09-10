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

func TestRedisReservations(t *testing.T) {
	connection := os.Getenv("TEST_REDIS_URL")
	if connection == "" {
		t.Skip("set TEST_REDIS_URL to run real Redis tests")
	}
	store, err := openRedis(connection)
	if err != nil {
		t.Fatal(err)
	}
	defer store.client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	prefix := fmt.Sprintf("test-reservation-%d", time.Now().UnixNano())
	sku := prefix + "-sku"
	keys := []string{"stock:" + sku, "reservation:" + prefix}
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), 3*time.Second)
		defer stop()
		if err := store.client.Del(cleanup, keys...).Err(); err != nil {
			t.Error(err)
		}
	}()
	if err := store.client.Set(ctx, "stock:"+sku, 10, 0).Err(); err != nil {
		t.Fatal(err)
	}
	request := reserveRequest{OrderID: prefix, SKU: sku, Quantity: 2}
	if replay, err := store.Reserve(ctx, request); err != nil || replay {
		t.Fatalf("create=%v %v", replay, err)
	}
	for i := 0; i < 3; i++ {
		if replay, err := store.Reserve(ctx, request); err != nil || !replay {
			t.Fatalf("replay=%v %v", replay, err)
		}
	}
	stock, err := store.client.Get(ctx, "stock:"+sku).Int()
	if err != nil || stock != 8 {
		t.Fatalf("stock=%d err=%v", stock, err)
	}
	reservation, err := store.client.HGetAll(ctx, "reservation:"+prefix).Result()
	if err != nil || reservation["sku"] != sku || reservation["quantity"] != "2" {
		t.Fatalf("reservation=%v err=%v", reservation, err)
	}
	changed := request
	changed.Quantity++
	if _, err := store.Reserve(ctx, changed); !errors.Is(err, errConflict) {
		t.Fatalf("quantity conflict=%v", err)
	}
	changed = request
	changed.SKU += "-other"
	if _, err := store.Reserve(ctx, changed); !errors.Is(err, errConflict) {
		t.Fatalf("sku conflict=%v", err)
	}
	changed = request
	changed.OrderID += "-too-many"
	changed.Quantity = 9
	keys = append(keys, "reservation:"+changed.OrderID)
	if _, err := store.Reserve(ctx, changed); !errors.Is(err, errInsufficientStock) {
		t.Fatalf("insufficient=%v", err)
	}
	if exists, err := store.client.Exists(ctx, "reservation:"+changed.OrderID).Result(); err != nil || exists != 0 {
		t.Fatalf("failed reservation exists: %d %v", exists, err)
	}
	changed.SKU += "-unknown"
	if _, err := store.Reserve(ctx, changed); !errors.Is(err, errUnknownSKU) {
		t.Fatalf("unknown=%v", err)
	}
	// Competing orders cannot oversell the remaining eight units.
	const count = 12
	var wg sync.WaitGroup
	outcomes := make(chan error, count)
	for i := 0; i < count; i++ {
		order := fmt.Sprintf("%s-competing-%d", prefix, i)
		keys = append(keys, "reservation:"+order)
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := store.Reserve(ctx, reserveRequest{OrderID: order, SKU: sku, Quantity: 1})
			outcomes <- err
		}()
	}
	wg.Wait()
	close(outcomes)
	succeeded := 0
	for err := range outcomes {
		if err == nil {
			succeeded++
		} else if !errors.Is(err, errInsufficientStock) && !errors.Is(err, errContention) {
			t.Error(err)
		}
	}
	stock, err = store.client.Get(ctx, "stock:"+sku).Int()
	if err != nil || stock < 0 || stock != 8-succeeded || succeeded == 0 {
		t.Fatalf("stock=%d successes=%d err=%v", stock, succeeded, err)
	}
	// Simultaneous identical requests must produce exactly one new reservation.
	duplicateSKU, duplicateOrder := sku+"-duplicate", prefix+"-duplicate"
	keys = append(keys, "stock:"+duplicateSKU, "reservation:"+duplicateOrder)
	if err := store.client.Set(ctx, "stock:"+duplicateSKU, 10, 0).Err(); err != nil {
		t.Fatal(err)
	}
	created := make(chan bool, count)
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			replay, err := store.Reserve(ctx, reserveRequest{OrderID: duplicateOrder, SKU: duplicateSKU, Quantity: 2})
			if err != nil {
				t.Error(err)
				return
			}
			created <- !replay
		}()
	}
	wg.Wait()
	close(created)
	newReservations := 0
	for value := range created {
		if value {
			newReservations++
		}
	}
	stock, err = store.client.Get(ctx, "stock:"+duplicateSKU).Int()
	if err != nil || stock != 8 || newReservations != 1 {
		t.Fatalf("duplicate stock=%d created=%d err=%v", stock, newReservations, err)
	}
	if err := store.Ping(ctx); err != nil {
		t.Fatal(err)
	}
	canceled, stop := context.WithCancel(ctx)
	stop()
	if _, err := store.Reserve(canceled, request); err == nil {
		t.Fatal("canceled operation succeeded")
	}
}
