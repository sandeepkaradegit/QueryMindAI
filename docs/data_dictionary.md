# QueryMind AI Demo Data Dictionary

## Dataset

The SQLite demo dataset models an e-commerce business. It is generated locally by `scripts/seed_db.py` with a fixed random seed, so repeated runs produce the same logical data. Money is stored as integer cents to avoid floating-point rounding errors. Timestamps use ISO 8601 UTC.

The six primary tables each contain at least 1,000 rows. `categories` is a small lookup table. The data spans order dates from 2024-01-01 through 2026-06-30.

## Tables

| Table | Purpose | Important columns and relationships |
|---|---|---|
| `categories` | Product classification lookup | `category_id` primary key; `name` is unique. |
| `customers` | Customer profiles | `customer_id` primary key; `email` is unique; `region`, `country`, and `signup_date` support segmentation. |
| `products` | Product catalog | `product_id` primary key; `category_id` references `categories`; `unit_price_cents` is the current catalog price. |
| `orders` | Customer purchase headers | `order_id` primary key; `customer_id` references `customers`; `order_date`, `status`, and `total_amount_cents` describe the purchase. |
| `order_items` | Products purchased on each order | `order_id` references `orders`; `product_id` references `products`; `quantity`, snapshot `unit_price_cents`, and `line_total_cents` define each line. One product appears at most once per order. |
| `payments` | Charge and refund events | `order_id` references `orders`; `payment_type` is `charge` or `refund`; `amount_cents`, `payment_method`, and `paid_at` describe the event. An order can have multiple payment events. |
| `shipments` | Fulfillment status per shipped order | `order_id` references `orders` and is unique; `status`, `shipped_at`, and optional `delivered_at` describe delivery. |

## Metric Definitions

- **Gross ordered revenue:** sum `order_items.line_total_cents`. This measures the value of items ordered before considering payment collection or refunds.
- **Order total:** `orders.total_amount_cents`, the stored total of an order's item lines in this dataset.
- **Net collected amount:** sum charge amounts minus refund amounts from `payments`; filter or group by `payment_type` explicitly.
- **Average order value:** average `orders.total_amount_cents`, optionally filtered by order status or date.
- **Order count:** count rows in `orders`; for distinct customers with orders, count distinct `customer_id`.

The words "revenue", "order total", and "amount paid" can refer to different measures in real businesses. For this demo, use gross ordered revenue for product sales and net collected amount for payment activity. The explicit definitions prevent silently treating payments and product sales as interchangeable.

## Relationships

```text
categories 1---many products
customers  1---many orders
orders     1---many order_items many---1 products
orders     1---many payments
orders     1---0..1 shipments
```

## Seed and Validate

From the repository root, using Python 3.10 or newer with `uv` installed:

```powershell
uv sync
uv run python scripts/seed_db.py
uv run python scripts/validate_database.py
```

The database is written to `data/querymind.sqlite3` by default. Both scripts accept `--db-path` to use a different database file. The validator checks the required table and row counts, foreign keys, order totals, shipment consistency, timestamp validity, the revenue ambiguity columns, and representative aggregate and temporal queries.