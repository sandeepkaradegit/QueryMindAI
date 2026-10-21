from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "database" / "schema.sql"
DEFAULT_DB_PATH = ROOT / "data" / "querymind.sqlite3"
SEED = 20260929

CATEGORIES = (
    ("Electronics", "Computers, audio, and connected devices"),
    ("Home", "Furniture and household essentials"),
    ("Kitchen", "Cookware and small appliances"),
    ("Outdoor", "Camping, garden, and outdoor gear"),
    ("Books", "Print and reference books"),
    ("Office", "Supplies and workspace equipment"),
    ("Fitness", "Exercise and wellness equipment"),
    ("Toys", "Games and children's toys"),
    ("Beauty", "Personal care and beauty products"),
    ("Apparel", "Clothing and accessories"),
    ("Footwear", "Shoes and footwear accessories"),
    ("Pet Supplies", "Food and accessories for pets"),
    ("Automotive", "Vehicle accessories and tools"),
    ("Garden", "Plants, tools, and garden supplies"),
    ("Travel", "Luggage and travel accessories"),
    ("Music", "Musical instruments and accessories"),
    ("Crafts", "Art and craft materials"),
    ("Baby", "Baby care and nursery products"),
    ("Grocery", "Packaged food and pantry goods"),
    ("Clearance", "Seasonal and end-of-line products"),
)
FIRST_NAMES = ("Alex", "Amira", "Chen", "Jordan", "Maya", "Noah", "Priya", "Sam", "Taylor", "Zoe")
LAST_NAMES = ("Anderson", "Bennett", "Chen", "Diaz", "Evans", "Foster", "Gupta", "Kim", "Morgan", "Patel")
REGIONS = ("North", "South", "East", "West", "Central")
COUNTRIES = ("US", "CA", "GB", "AU")
PAYMENT_METHODS = ("card", "bank_transfer", "wallet")
CARRIERS = ("ParcelPost", "Northstar", "SwiftShip", "MetroCourier")


def timestamp(rng: random.Random, start: date, end: date) -> str:
    days = (end - start).days
    selected_date = start + timedelta(days=rng.randint(0, days))
    selected_time = time(rng.randrange(24), rng.randrange(60), rng.randrange(60))
    return datetime.combine(selected_date, selected_time, timezone.utc).isoformat()


def date_timestamp(value: date, rng: random.Random) -> str:
    return datetime.combine(value, time(rng.randrange(24), rng.randrange(60), rng.randrange(60)), timezone.utc).isoformat()


def seed_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute("DELETE FROM shipments")
        connection.execute("DELETE FROM payments")
        connection.execute("DELETE FROM order_items")
        connection.execute("DELETE FROM orders")
        connection.execute("DELETE FROM products")
        connection.execute("DELETE FROM customers")
        connection.execute("DELETE FROM categories")

        connection.executemany(
            "INSERT INTO categories (category_id, name, description) VALUES (?, ?, ?)",
            [(category_id, *category) for category_id, category in enumerate(CATEGORIES, start=1)],
        )

        customers = []
        for customer_id in range(1, 1201):
            first_name = FIRST_NAMES[(customer_id - 1) % len(FIRST_NAMES)]
            last_name = LAST_NAMES[((customer_id - 1) // len(FIRST_NAMES)) % len(LAST_NAMES)]
            signup_date = timestamp(rng, date(2021, 1, 1), date(2025, 12, 31))
            customers.append(
                (
                    customer_id,
                    f"{first_name} {last_name}",
                    f"customer{customer_id:04d}@example.test",
                    REGIONS[(customer_id - 1) % len(REGIONS)],
                    COUNTRIES[(customer_id - 1) % len(COUNTRIES)],
                    signup_date,
                )
            )
        connection.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers
        )

        products = []
        prices: dict[int, int] = {}
        product_names = ("Essential", "Classic", "Plus", "Compact", "Pro", "Everyday")
        for product_id in range(1, 1201):
            category_id = ((product_id - 1) % len(CATEGORIES)) + 1
            price_cents = rng.randint(499, 249_999)
            prices[product_id] = price_cents
            products.append(
                (
                    product_id,
                    category_id,
                    f"SKU-{product_id:05d}",
                    f"{CATEGORIES[category_id - 1][0]} {product_names[(product_id - 1) % len(product_names)]} {product_id:04d}",
                    price_cents,
                    timestamp(rng, date(2020, 1, 1), date(2025, 12, 31)),
                )
            )
        connection.executemany(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)", products
        )

        order_rows = []
        item_rows = []
        payment_rows = []
        shipment_rows = []
        first_order_date = date(2024, 1, 1)
        last_order_date = date(2026, 6, 30)

        for order_id in range(1, 1601):
            customer_id = order_id if order_id <= 1200 else rng.randint(1, 1200)
            order_datetime = timestamp(rng, first_order_date, last_order_date)
            order_day = datetime.fromisoformat(order_datetime).date()
            if order_id <= 1250:
                status = rng.choice(("shipped", "delivered"))
            else:
                status = rng.choices(
                    ("paid", "processing", "cancelled", "refunded"),
                    weights=(35, 35, 15, 15),
                    k=1,
                )[0]

            item_count = rng.randint(1, 4)
            selected_products = []
            if order_id <= 1200:
                selected_products.append(order_id)
            remaining_count = item_count - len(selected_products)
            if remaining_count:
                candidates = [product_id for product_id in range(1, 1201) if product_id not in selected_products]
                selected_products.extend(rng.sample(candidates, remaining_count))

            order_total_cents = 0
            for product_id in selected_products:
                quantity = rng.randint(1, 5)
                unit_price_cents = prices[product_id]
                line_total_cents = quantity * unit_price_cents
                order_total_cents += line_total_cents
                item_rows.append(
                    (
                        len(item_rows) + 1,
                        order_id,
                        product_id,
                        quantity,
                        unit_price_cents,
                        line_total_cents,
                    )
                )

            order_rows.append((order_id, customer_id, order_datetime, status, order_total_cents))

            if status != "cancelled":
                payment_rows.append(
                    (
                        len(payment_rows) + 1,
                        order_id,
                        "charge",
                        rng.choice(PAYMENT_METHODS),
                        order_total_cents,
                        date_timestamp(order_day + timedelta(days=rng.randint(0, 3)), rng),
                    )
                )
                if status == "refunded":
                    refund_cents = max(1, rng.randint(25, 100) * order_total_cents // 100)
                    payment_rows.append(
                        (
                            len(payment_rows) + 1,
                            order_id,
                            "refund",
                            rng.choice(PAYMENT_METHODS),
                            refund_cents,
                            date_timestamp(order_day + timedelta(days=rng.randint(1, 14)), rng),
                        )
                    )

            if status in ("shipped", "delivered"):
                shipped_day = order_day + timedelta(days=rng.randint(1, 5))
                shipment_status = "delivered" if status == "delivered" else "in_transit"
                delivered_at = None
                if shipment_status == "delivered":
                    delivered_day = shipped_day + timedelta(days=rng.randint(1, 8))
                    delivered_at = date_timestamp(delivered_day, rng)
                shipment_rows.append(
                    (
                        len(shipment_rows) + 1,
                        order_id,
                        rng.choice(CARRIERS),
                        f"TRK-{order_id:08d}",
                        shipment_status,
                        date_timestamp(shipped_day, rng),
                        delivered_at,
                    )
                )

        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", order_rows)
        connection.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", item_rows)
        connection.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)", payment_rows)
        connection.executemany("INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?, ?)", shipment_rows)

    print(f"Seeded database: {db_path}")
    print(f"Deterministic seed: {SEED}")
    for table in ("categories", "customers", "products", "orders", "order_items", "payments", "shipments"):
        with sqlite3.connect(db_path) as connection:
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the deterministic QueryMind demo database.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    seed_database(args.db_path.resolve())


if __name__ == "__main__":
    main()