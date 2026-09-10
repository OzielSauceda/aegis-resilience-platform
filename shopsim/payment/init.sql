CREATE TABLE IF NOT EXISTS payments (
    order_id TEXT PRIMARY KEY CHECK (length(btrim(order_id)) > 0),
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    status TEXT NOT NULL CHECK (status = 'charged'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
