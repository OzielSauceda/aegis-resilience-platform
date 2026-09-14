package main

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/telemetry"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

var (
	errConflict          = errors.New("order already reserved with different details")
	errInsufficientStock = errors.New("insufficient stock")
	errUnknownSKU        = errors.New("unknown sku")
	errContention        = errors.New("reservation transaction retry limit reached")
)

type inventoryStore interface {
	Reserve(context.Context, reserveRequest) (replayed bool, err error)
	Ping(context.Context) error
}

type redisStore struct{ client *redis.Client }

func openRedis(connection string) (*redisStore, error) {
	options, err := redis.ParseURL(connection)
	if err != nil {
		return nil, err
	}
	options.PoolSize = 4
	options.MaxActiveConns = 4
	options.DialTimeout = 500 * time.Millisecond
	options.ReadTimeout = 500 * time.Millisecond
	options.WriteTimeout = 500 * time.Millisecond
	options.PoolTimeout = 500 * time.Millisecond
	options.ContextTimeoutEnabled = true
	options.MaxRetries = -1 // Retry only known WATCH conflicts, never ambiguous writes.
	return &redisStore{client: redis.NewClient(options)}, nil
}

func (s *redisStore) Ping(ctx context.Context) error { return s.client.Ping(ctx).Err() }

// SETNX seeds missing stock only. Restarting Inventory cannot replenish a SKU.
func (s *redisStore) Seed(ctx context.Context) error {
	for sku, quantity := range map[string]int{"sku-001": 100, "sku-002": 50, "sku-003": 25} {
		if err := s.client.SetNX(ctx, "stock:"+sku, quantity, 0).Err(); err != nil {
			return err
		}
	}
	return nil
}

func (s *redisStore) Reserve(ctx context.Context, request reserveRequest) (replay bool, resultErr error) {
	ctx, span := otel.Tracer("shopsim/inventory").Start(ctx, "redis.reserve", trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(attribute.String("db.system.name", "redis"), attribute.String("db.operation.name", "reserve"),
			attribute.String("shopsim.order_id", request.OrderID), attribute.String("shopsim.sku", request.SKU), attribute.Int("shopsim.quantity", request.Quantity)))
	defer func() {
		outcome := "created"
		switch {
		case errors.Is(resultErr, errConflict):
			outcome = "conflict"
		case errors.Is(resultErr, errInsufficientStock):
			outcome = "insufficient_stock"
		case errors.Is(resultErr, errUnknownSKU):
			outcome = "unknown_sku"
		case resultErr != nil:
			outcome = "storage_error"
			telemetry.StorageError(span, "redis", resultErr)
		case replay:
			outcome = "replayed"
		}
		span.SetAttributes(attribute.String("shopsim.outcome", outcome))
		span.End()
	}()
	stockKey := "stock:" + request.SKU
	reservationKey := "reservation:" + request.OrderID
	const maxAttempts = 8
	for attempt := 0; attempt < maxAttempts; attempt++ {
		span.SetAttributes(attribute.Int("shopsim.transaction_attempts", attempt+1))
		replayed := false
		err := s.client.Watch(ctx, func(tx *redis.Tx) error {
			existing, err := tx.HGetAll(ctx, reservationKey).Result()
			if err != nil {
				return err
			}
			if len(existing) != 0 {
				if existing["sku"] != request.SKU || existing["quantity"] != strconv.Itoa(request.Quantity) {
					return errConflict
				}
				replayed = true
				return nil
			}
			stock, err := tx.Get(ctx, stockKey).Int64()
			if errors.Is(err, redis.Nil) {
				return errUnknownSKU
			}
			if err != nil {
				return err
			}
			if stock < int64(request.Quantity) {
				return errInsufficientStock
			}
			// WATCH covers both keys: competing orders and duplicate orders abort
			// before either write. SET uses the already-validated numeric stock.
			_, err = tx.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
				pipe.Set(ctx, stockKey, stock-int64(request.Quantity), 0)
				pipe.HSet(ctx, reservationKey, "sku", request.SKU, "quantity", request.Quantity)
				return nil
			})
			return err
		}, stockKey, reservationKey)
		if !errors.Is(err, redis.TxFailedErr) {
			return replayed, err
		}
		if attempt+1 < maxAttempts {
			timer := time.NewTimer(time.Duration(attempt+1) * 5 * time.Millisecond)
			select {
			case <-ctx.Done():
				timer.Stop()
				return false, ctx.Err()
			case <-timer.C:
			}
		}
	}
	return false, errContention
}
