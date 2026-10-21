PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL,
    country TEXT NOT NULL,
    signup_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(category_id),
    sku TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'processing', 'shipped', 'delivered', 'cancelled', 'refunded')),
    total_amount_cents INTEGER NOT NULL CHECK (total_amount_cents >= 0)
);

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0),
    line_total_cents INTEGER NOT NULL CHECK (line_total_cents = quantity * unit_price_cents),
    UNIQUE (order_id, product_id)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    payment_type TEXT NOT NULL CHECK (payment_type IN ('charge', 'refund')),
    payment_method TEXT NOT NULL CHECK (payment_method IN ('card', 'bank_transfer', 'wallet')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    paid_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id),
    carrier TEXT NOT NULL,
    tracking_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('in_transit', 'delivered')),
    shipped_at TEXT NOT NULL,
    delivered_at TEXT,
    CHECK ((status = 'delivered' AND delivered_at IS NOT NULL) OR (status = 'in_transit' AND delivered_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_orders_customer_date ON orders(customer_id, order_date);
CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);