"""Seed demo stores + items into the database, for trying out the buyer
marketplace end to end without manually onboarding a bunch of sellers.

Usage:
    Make sure DATABASE_URL is set (same .env as running the app locally),
    then:
        python seed_demo_stores.py

Every store below uses:
    Owner name    : Test Test
    Business PIN  : 1111
    Personal PIN  : 1111
    WhatsApp      : a fake +255 number per store (edit before going live)

Safe to re-run: a store is skipped if a business with that name already
exists, so running this twice won't create duplicates.
"""
import uuid
from datetime import datetime

from werkzeug.security import generate_password_hash

import app as enzi_app

OWNER_FIRST = "Test"
OWNER_LAST = "Test"
PIN = "1111"

STORES = [
    {
        "business_name": "Dar Electronics Hub",
        "location": "Kariakoo Market",
        "region": "Dar es Salaam",
        "whatsapp_number": "+255700111001",
        "items": [
            {
                "item_name": "Wireless Headphones",
                "item_type": "Over-ear",
                "item_color": "Black",
                "quantity": 12,
                "price_per_unit": 25000,
                "photo_url": "https://images.unsplash.com/photo-1576082712237-eb1335ce23a3?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Smartphone",
                "item_type": "Android",
                "item_color": "Black",
                "quantity": 6,
                "price_per_unit": 450000,
                "photo_url": "https://images.unsplash.com/photo-1571126770247-9a99e5f7eff7?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Smartwatch",
                "item_type": "Fitness tracker",
                "item_color": "Black",
                "quantity": 9,
                "price_per_unit": 120000,
                "photo_url": "https://images.unsplash.com/photo-1434494745656-1aea7daa8f6f?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Glow Beauty & Makeup",
        "location": "Kaloleni",
        "region": "Arusha",
        "whatsapp_number": "+255700111002",
        "items": [
            {
                "item_name": "Makeup Brush Set",
                "item_type": "Cosmetics",
                "quantity": 20,
                "price_per_unit": 18000,
                "photo_url": "https://images.unsplash.com/photo-1764333746618-6285bf70db23?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Matte Red Lipstick",
                "item_type": "Lipstick",
                "item_color": "Red",
                "quantity": 30,
                "price_per_unit": 9500,
                "photo_url": "https://images.unsplash.com/photo-1532441807072-e075a14e3b69?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Zawadi Ladies Fashion",
        "location": "Kirumba Market",
        "region": "Mwanza",
        "whatsapp_number": "+255700111003",
        "items": [
            {
                "item_name": "Evening Gown",
                "item_type": "Dress",
                "item_color": "Black",
                "quantity": 5,
                "price_per_unit": 85000,
                "photo_url": "https://images.unsplash.com/photo-1554881070-74595ca2b74c?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Chiffon Blouse",
                "item_type": "Blouse",
                "quantity": 15,
                "price_per_unit": 35000,
                "photo_url": "https://images.unsplash.com/photo-1554881070-74595ca2b74c?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Kilimanjaro Men's Collection",
        "location": "Soweto Market",
        "region": "Kilimanjaro",
        "whatsapp_number": "+255700111004",
        "items": [
            {
                "item_name": "Classic Suit",
                "item_type": "Suit",
                "item_color": "Navy",
                "quantity": 4,
                "price_per_unit": 150000,
                "photo_url": "https://images.unsplash.com/photo-1495264537403-93658651aaea?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Denim Jacket",
                "item_type": "Jacket",
                "item_color": "Blue",
                "quantity": 10,
                "price_per_unit": 45000,
                "photo_url": "https://images.unsplash.com/photo-1597815413302-8037c47f5a75?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Tanga Fragrance House",
        "location": "Ngamiani",
        "region": "Tanga",
        "whatsapp_number": "+255700111005",
        "items": [
            {
                "item_name": "Signature Eau de Parfum",
                "item_type": "Perfume",
                "item_size": "50ml",
                "quantity": 14,
                "price_per_unit": 60000,
                "photo_url": "https://images.unsplash.com/photo-1543422655-ac1c6ca993ed?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Oud Rose Perfume",
                "item_type": "Perfume",
                "item_size": "30ml",
                "quantity": 8,
                "price_per_unit": 75000,
                "photo_url": "https://images.unsplash.com/photo-1543422655-ac1c6ca993ed?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Mbeya Bags & Leather",
        "location": "Soweto",
        "region": "Mbeya",
        "whatsapp_number": "+255700111006",
        "items": [
            {
                "item_name": "Leather Bucket Bag",
                "item_type": "Bucket bag",
                "quantity": 7,
                "price_per_unit": 55000,
                "photo_url": "https://images.unsplash.com/photo-1562176603-b64adfb882fd?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Brown Leather Handbag",
                "item_type": "Handbag",
                "item_color": "Brown",
                "quantity": 9,
                "price_per_unit": 65000,
                "photo_url": "https://images.unsplash.com/photo-1598532163257-ae3c6b2524b6?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Dodoma Shoe Palace",
        "location": "Manzese",
        "region": "Dodoma",
        "whatsapp_number": "+255700111007",
        "items": [
            {
                "item_name": "White Sneakers",
                "item_type": "Sneakers",
                "item_color": "White",
                "quantity": 16,
                "price_per_unit": 40000,
                "photo_url": "https://images.unsplash.com/photo-1676379827610-c380c52db0c6?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Classic White Trainers",
                "item_type": "Trainers",
                "item_color": "White",
                "quantity": 11,
                "price_per_unit": 38000,
                "photo_url": "https://images.unsplash.com/photo-1676379827610-c380c52db0c6?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
    {
        "business_name": "Zanzibar Gold & Jewelry",
        "location": "Stone Town",
        "region": "Mjini Magharibi",
        "whatsapp_number": "+255700111008",
        "items": [
            {
                "item_name": "Gold Necklace & Earring Set",
                "item_type": "Jewelry set",
                "item_color": "Gold",
                "quantity": 6,
                "price_per_unit": 200000,
                "photo_url": "https://images.unsplash.com/photo-1758995115682-1452a1a9e35b?w=800&q=80&fit=crop&auto=format",
            },
            {
                "item_name": "Gold Stud Earrings",
                "item_type": "Earrings",
                "item_color": "Gold",
                "quantity": 13,
                "price_per_unit": 85000,
                "photo_url": "https://images.unsplash.com/photo-1758995115682-1452a1a9e35b?w=800&q=80&fit=crop&auto=format",
            },
        ],
    },
]


def seed_into(db):
    """Does the actual seeding against an already-open db handle, inside an
    already-active Flask app context (from a request or from `seed()` below).
    Returns a list of one result line per store, for the caller to display."""
    performed_by_name = f"{OWNER_FIRST} {OWNER_LAST}"
    results = []

    for store in STORES:
        business_key = store["business_name"].strip().lower()
        existing = db.execute(
            "SELECT id FROM businesses WHERE business_key = ?", (business_key,)
        ).fetchone()
        if existing:
            results.append(f"Skipping '{store['business_name']}' — already exists.")
            continue

        business_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            INSERT INTO businesses (id, business_key, business_name, default_location, country, region,
                                     pin_hash, whatsapp_number, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                business_id, business_key, store["business_name"], store["location"], "Tanzania",
                store["region"], generate_password_hash(PIN), store["whatsapp_number"], now,
            ),
        )

        user_id = uuid.uuid4().hex
        user_key = enzi_app.make_user_key(OWNER_FIRST, OWNER_LAST)
        db.execute(
            """
            INSERT INTO users (id, business_id, user_key, first_name, last_name, pin_hash,
                                last_login_at, language, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, business_id, user_key, OWNER_FIRST, OWNER_LAST, generate_password_hash(PIN), now, "en", now),
        )

        for item in store["items"]:
            enzi_app._create_new_item(
                db, business_id, user_id, performed_by_name, store["location"],
                item_name=item["item_name"],
                item_type=item.get("item_type", ""),
                item_color=item.get("item_color", ""),
                item_brand=item.get("item_brand", ""),
                item_size=item.get("item_size", ""),
                item_code=item.get("item_code", ""),
                location=store["location"],
                photo_url=item.get("photo_url"),
                quantity=item["quantity"],
                price_per_unit=item["price_per_unit"],
            )

        db.commit()
        results.append(f"Created '{store['business_name']}' ({store['region']}) with {len(store['items'])} items.")

    return results


def seed():
    """CLI entry point: opens its own app context and db connection."""
    with enzi_app.app.app_context():
        db = enzi_app.get_db()
        for line in seed_into(db):
            print(line)
        print("\nDone. Log in to any store with:")
        print(f"  Business PIN : {PIN}")
        print(f"  Owner name   : {OWNER_FIRST} {OWNER_LAST}")
        print(f"  Personal PIN : {PIN}")


if __name__ == "__main__":
    seed()
