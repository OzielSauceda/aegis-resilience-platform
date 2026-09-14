package main

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/OzielSauceda/aegis-resilience-platform/shopsim/internal/telemetry"
	_ "github.com/jackc/pgx/v5/stdlib"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

var errConflict = errors.New("order already charged with a different amount")

type paymentStore interface {
	Charge(context.Context, chargeRequest) (replayed bool, err error)
	Ping(context.Context) error
}

type postgresStore struct{ db *sql.DB }

func openPostgres(connection string) (*postgresStore, error) {
	db, err := sql.Open("pgx", connection)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)
	return &postgresStore{db: db}, nil
}

func (s *postgresStore) Ping(ctx context.Context) error { return s.db.PingContext(ctx) }

func (s *postgresStore) Charge(ctx context.Context, request chargeRequest) (replay bool, resultErr error) {
	ctx, span := otel.Tracer("shopsim/payment").Start(ctx, "postgresql.charge", trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(attribute.String("db.system.name", "postgresql"), attribute.String("db.operation.name", "charge"),
			attribute.String("shopsim.order_id", request.OrderID), attribute.Int64("shopsim.amount_cents", request.AmountCents)))
	defer func() {
		outcome := "created"
		switch {
		case errors.Is(resultErr, errConflict):
			outcome = "conflict"
		case resultErr != nil:
			outcome = "storage_error"
			telemetry.StorageError(span, "postgresql", resultErr)
		case replay:
			outcome = "replayed"
		}
		span.SetAttributes(attribute.String("shopsim.outcome", outcome))
		span.End()
	}()
	var orderID string
	err := s.db.QueryRowContext(ctx, `
		INSERT INTO payments (order_id, amount_cents, status)
		VALUES ($1, $2, 'charged')
		ON CONFLICT (order_id) DO NOTHING
		RETURNING order_id`, request.OrderID, request.AmountCents).Scan(&orderID)
	if err == nil {
		return false, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return false, err
	}
	// A separate statement sees the committed winner after a concurrent INSERT.
	// The application never updates or deletes ledger rows.
	var amount int64
	var status string
	err = s.db.QueryRowContext(ctx, `SELECT amount_cents, status FROM payments WHERE order_id = $1`, request.OrderID).Scan(&amount, &status)
	if err != nil {
		return false, err
	}
	if amount != request.AmountCents {
		return false, errConflict
	}
	if status != "charged" {
		return false, errors.New("unexpected stored payment status")
	}
	return true, nil
}
