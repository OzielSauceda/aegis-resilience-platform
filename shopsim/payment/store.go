package main

import (
	"context"
	"database/sql"
	"errors"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
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

func (s *postgresStore) Charge(ctx context.Context, request chargeRequest) (bool, error) {
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
