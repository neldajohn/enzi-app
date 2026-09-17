import csv
import io
import os
import re
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from flask import Flask, Response, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from countries import COUNTRIES, REMOVAL_REASONS, TANZANIA_REGIONS
from translations import translate

load_dotenv()

app = Flask(__name__)
app.secret_key = "enzi-stage1-dev-key"

# Vercel's Postgres/Neon integration doesn't always name this DATABASE_URL
# depending on how it was added to the project, so fall back to the other
# plain libpq-compatible names it commonly uses instead (POSTGRES_PRISMA_URL
# is deliberately not included here — it carries Prisma-specific query
# params like pgbouncer=true that psycopg2 doesn't understand).
DATABASE_URL = (
    os.environ.get("DATABASE_URL", "").strip()
    or os.environ.get("POSTGRES_URL", "").strip()
    or os.environ.get("POSTGRES_URL_NON_POOLING", "").strip()
)

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "").strip()

FEEDBACK_TO_EMAIL = "dukaniadmin@gmail.com"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = "onboarding@resend.dev"


class PGConnection:
    """Thin wrapper so the rest of the app can keep using SQLite-style
    `db.execute(query_with_question_marks, params).fetchone()` chaining
    against a real psycopg2/PostgreSQL connection underneath."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(re.sub(r"\?", "%s", query), params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _connect():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it to your PostgreSQL connection string "
            "(see README.md / render.yaml for setup)."
        )
    return psycopg2.connect(DATABASE_URL)


def get_db():
    if "db" not in g:
        g.db = PGConnection(_connect())
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        if exception is not None:
            db.rollback()
        db.close()


def init_db():
    db = PGConnection(_connect())

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS businesses (
            id TEXT PRIMARY KEY,
            business_key TEXT UNIQUE NOT NULL,
            business_name TEXT NOT NULL,
            default_location TEXT,
            country TEXT,
            region TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            user_key TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(business_id, user_key)
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_type TEXT,
            item_color TEXT,
            item_brand TEXT,
            item_code TEXT,
            location TEXT,
            photo_url TEXT,
            quantity INTEGER NOT NULL,
            reserved_quantity INTEGER NOT NULL DEFAULT 0,
            price_per_unit REAL NOT NULL,
            created_by_user_id TEXT,
            created_by_name TEXT,
            last_added_by_user_id TEXT,
            last_added_by_name TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sales (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity_sold INTEGER NOT NULL,
            sale_price REAL NOT NULL,
            customer_name TEXT NOT NULL,
            customer_key TEXT NOT NULL,
            sold_by_user_id TEXT,
            sold_by_name TEXT,
            sold_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS item_history (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            change_type TEXT NOT NULL,
            quantity_change INTEGER NOT NULL,
            resulting_quantity INTEGER NOT NULL,
            performed_by_user_id TEXT,
            performed_by_name TEXT,
            occurred_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            agreed_price REAL NOT NULL,
            customer_name TEXT NOT NULL,
            customer_key TEXT NOT NULL,
            reserved_by_user_id TEXT,
            reserved_by_name TEXT,
            reserved_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS removals (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            reason TEXT NOT NULL,
            note TEXT,
            removed_by_user_id TEXT,
            removed_by_name TEXT,
            removed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            business_name TEXT NOT NULL,
            user_id TEXT,
            user_name TEXT NOT NULL,
            message TEXT NOT NULL,
            email_sent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS incoming_stock (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            item_type TEXT,
            item_color TEXT,
            item_brand TEXT,
            item_size TEXT,
            item_code TEXT,
            location TEXT,
            photo_url TEXT,
            quantity INTEGER NOT NULL,
            price_per_unit REAL NOT NULL,
            expected_date TEXT,
            added_by_user_id TEXT,
            added_by_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS restocks (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_key TEXT NOT NULL,
            date_requested TEXT NOT NULL,
            requested_by_user_id TEXT,
            requested_by_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS wishlist (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            added_by_user_id TEXT,
            added_by_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_value_history (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            total_value REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            name TEXT NOT NULL,
            contact_phone TEXT,
            contact_whatsapp TEXT,
            typically_supplies TEXT,
            created_by_user_id TEXT,
            created_by_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            business_id TEXT NOT NULL,
            name TEXT NOT NULL,
            customer_key TEXT NOT NULL,
            whatsapp_number TEXT,
            delivery_details TEXT,
            notes TEXT,
            created_by_user_id TEXT,
            created_by_name TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(business_id, customer_key)
        )
        """
    )

    # Column migrations for tables that may already exist from an earlier deploy.
    # PostgreSQL supports "ADD COLUMN IF NOT EXISTS" natively, so these can just
    # run unconditionally on every startup instead of checking first.
    for col, ddl in [
        ("item_brand", "TEXT"),
        ("item_size", "TEXT"),
        ("reserved_quantity", "INTEGER NOT NULL DEFAULT 0"),
        ("last_added_by_user_id", "TEXT"),
        ("last_added_by_name", "TEXT"),
        ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
        ("deleted_by_user_id", "TEXT"),
        ("deleted_by_name", "TEXT"),
        ("deleted_at", "TEXT"),
        ("deleted_reason", "TEXT"),
        ("notes", "TEXT"),
    ]:
        db.execute(f"ALTER TABLE items ADD COLUMN IF NOT EXISTS {col} {ddl}")
    db.execute(
        "UPDATE items SET last_added_by_user_id = created_by_user_id, last_added_by_name = created_by_name "
        "WHERE last_added_by_name IS NULL"
    )

    db.execute("ALTER TABLE incoming_stock ADD COLUMN IF NOT EXISTS expected_date TEXT")
    db.execute("ALTER TABLE incoming_stock ADD COLUMN IF NOT EXISTS vendor_id TEXT")
    db.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS vendor_id TEXT")

    for col in ("country", "region"):
        db.execute(f"ALTER TABLE businesses ADD COLUMN IF NOT EXISTS {col} TEXT")

    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'")
    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS expected_sale_date TEXT")

    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TEXT")
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'en'")

    for col in ("link_url", "photo_url", "notes"):
        db.execute(f"ALTER TABLE wishlist ADD COLUMN IF NOT EXISTS {col} TEXT")

    for col in ("contact_email", "contact_whatsapp"):
        db.execute(f"ALTER TABLE feedback ADD COLUMN IF NOT EXISTS {col} TEXT")

    db.execute("ALTER TABLE sales ADD COLUMN IF NOT EXISTS customer_id TEXT")
    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS customer_id TEXT")
    db.execute("ALTER TABLE restocks ADD COLUMN IF NOT EXISTS customer_id TEXT")

    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS pin_hash TEXT")
    db.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS contact_email TEXT")
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS theme_preset TEXT")
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS logo_url TEXT")
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS theme_custom_color TEXT")
    db.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS contact_instagram TEXT")
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pin_hash TEXT")
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trusted_device_token TEXT")
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS added_by_user_id TEXT")
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS added_by_name TEXT")

    # Enzi buyer storefront: seller's WhatsApp contact, and where a
    # reservation came from plus the buyer's delivery details when it was
    # placed directly by a buyer through the storefront rather than by the
    # seller in person.
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_number TEXT")
    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'seller'")
    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS buyer_delivery_address TEXT")
    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS buyer_availability TEXT")
    db.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS buyer_delivery_method TEXT")

    # Internal-only 9-character account number per business, for telling apart
    # two businesses that happen to share a name — never shown to buyers.
    # Sequence starts at a run of 1s (111111111) per spec, then increments.
    db.execute("CREATE SEQUENCE IF NOT EXISTS business_account_number_seq START 111111111")
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS account_number TEXT")
    unnumbered = db.execute(
        "SELECT id FROM businesses WHERE account_number IS NULL ORDER BY created_at ASC"
    ).fetchall()
    for row in unnumbered:
        db.execute(
            "UPDATE businesses SET account_number = lpad(nextval('business_account_number_seq')::text, 9, '0') "
            "WHERE id = ?",
            (row["id"],),
        )

    # Personal contact info (separate from the business's own buyer-facing
    # WhatsApp number above) — used for account-recovery / lockout alerts.
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_whatsapp TEXT")
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_email TEXT")

    # Progressive login lockout, tracked per business account (shared across
    # the business-PIN and personal-PIN steps, regardless of device).
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS failed_login_locked_until TEXT")
    db.execute("ALTER TABLE businesses ADD COLUMN IF NOT EXISTS failed_login_lockout_rounds INTEGER NOT NULL DEFAULT 0")

    db.commit()
    db.close()


def make_user_key(first_name, last_name):
    return f"{first_name.strip().lower()}|{last_name.strip().lower()}"


# Progressive login lockout — shared across the business-PIN and personal-PIN
# steps and across every teammate, since it's scoped to the business account
# rather than to a device or an individual user. No delay for the first 3
# wrong attempts; attempt 4 costs 30s, attempt 5 costs 2min, attempt 6+ costs
# 15min each, repeating forever rather than ever fully locking the account.
def _lockout_wait_seconds(attempt_count):
    if attempt_count == 4:
        return 30
    if attempt_count == 5:
        return 120
    if attempt_count >= 6:
        return 900
    return 0


def check_business_lockout(business):
    """Returns a translated error message if this business is currently
    locked out, or None if a login attempt is allowed to proceed."""
    locked_until_raw = business["failed_login_locked_until"]
    if not locked_until_raw:
        return None
    try:
        locked_until = datetime.fromisoformat(locked_until_raw)
    except ValueError:
        return None
    remaining = (locked_until - datetime.now()).total_seconds()
    if remaining <= 0:
        return None
    remaining = int(remaining) + 1
    wait_text = t("wait_seconds", seconds=remaining) if remaining < 60 else t("wait_minutes", minutes=(remaining + 59) // 60)
    return t("err_login_locked", wait=wait_text)


def record_failed_login(db, business):
    new_count = business["failed_login_count"] + 1
    wait_seconds = _lockout_wait_seconds(new_count)
    locked_until = (
        (datetime.now() + timedelta(seconds=wait_seconds)).isoformat(timespec="seconds") if wait_seconds else None
    )
    db.execute(
        "UPDATE businesses SET failed_login_count = ?, failed_login_locked_until = ? WHERE id = ?",
        (new_count, locked_until, business["id"]),
    )
    db.commit()


def clear_failed_login(db, business_id):
    db.execute(
        "UPDATE businesses SET failed_login_count = 0, failed_login_locked_until = NULL WHERE id = ?",
        (business_id,),
    )
    db.commit()


def title_case(value):
    if not value:
        return value
    return " ".join(word[:1].upper() + word[1:].lower() for word in value.split(" "))


def format_datetime(value):
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%b %d, %Y %I:%M %p")


# Sellers are in Tanzania, but the server isn't necessarily — greetings are
# computed against Tanzania's own clock (EAT, UTC+3, no DST) rather than
# whatever timezone happens to be hosting the app.
EAT = ZoneInfo("Africa/Dar_es_Salaam")


def time_based_greeting():
    hour = datetime.now(EAT).hour
    if 5 <= hour < 12:
        return t("greeting_morning")
    if 12 <= hour < 17:
        return t("greeting_afternoon")
    return t("greeting_evening")


def _clamp_text(value, max_len):
    return (value or "").strip()[:max_len]


PHONE_RE = re.compile(r"^\+?[0-9\s\-()]{7,20}$")


def _valid_phone(value):
    """A phone/WhatsApp field is optional, so empty is valid. If given, it must
    look like a real number: only digits/spaces/dashes/parens (+ optional
    leading +), with 7-15 actual digits (roughly the E.164 range)."""
    value = (value or "").strip()
    if not value:
        return True
    if not PHONE_RE.match(value):
        return False
    digit_count = len(re.sub(r"\D", "", value))
    return 7 <= digit_count <= 15


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(value):
    value = (value or "").strip()
    if not value:
        return True
    return bool(EMAIL_RE.match(value))


def whatsapp_digits(phone):
    return re.sub(r"\D", "", phone or "")


def build_whatsapp_link(phone, message):
    """Builds a wa.me deep link that opens WhatsApp with `message` pre-filled.
    Returns None if there's no usable phone number — the caller decides how
    to handle a seller who hasn't set up WhatsApp yet."""
    digits = whatsapp_digits(phone)
    if not digits:
        return None
    return f"https://wa.me/{digits}?text={quote(message)}"


def item_descriptor(item):
    """'Name (type, color)' for WhatsApp messages, dropping type/color when unset."""
    details = [d for d in (item["item_type"], item["item_color"]) if d]
    if details:
        return f"{item['item_name']} ({', '.join(details)})"
    return item["item_name"]


def format_price(amount):
    return "{:,.0f} TZS".format(amount)


app.jinja_env.filters["titlecase"] = title_case
app.jinja_env.filters["dt"] = format_datetime


def t(key, **kwargs):
    return translate(key, "en", **kwargs)


app.jinja_env.globals["t"] = t


@app.context_processor
def inject_business():
    """Makes `business`, `user`, `theme`, and `greeting` available to every
    template automatically (for the persistent header and brand styling),
    without every route needing to fetch and pass them."""
    business_id = session.get("business_id")
    if not business_id:
        return {"theme": get_theme(None)}
    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    user_id = session.get("user_id")
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    return {"business": business, "user": user, "theme": get_theme(business), "greeting": time_based_greeting()}


def find_matching_item(db, business_id, item_name, item_type, item_color, item_brand, item_size, item_code):
    candidates = db.execute(
        "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0", (business_id,)
    ).fetchall()
    name_key = item_name.strip().lower()
    type_key = (item_type or "").strip().lower()
    color_key = (item_color or "").strip().lower()
    brand_key = (item_brand or "").strip().lower()
    size_key = (item_size or "").strip().lower()
    code_key = (item_code or "").strip().lower()
    for candidate in candidates:
        if (
            candidate["item_name"].strip().lower() == name_key
            and (candidate["item_type"] or "").strip().lower() == type_key
            and (candidate["item_color"] or "").strip().lower() == color_key
            and (candidate["item_brand"] or "").strip().lower() == brand_key
            and (candidate["item_size"] or "").strip().lower() == size_key
            and (candidate["item_code"] or "").strip().lower() == code_key
        ):
            return candidate
    return None


def send_feedback_email(subject, body):
    if not RESEND_API_KEY:
        return False, t("err_feedback_email_not_configured")
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [FEEDBACK_TO_EMAIL],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
        if not response.ok:
            print(f"[feedback email] Resend API {response.status_code}: {response.text}", flush=True)
        response.raise_for_status()
        return True, None
    except requests.RequestException as exc:
        print(f"[feedback email] send failed: {exc}", flush=True)
        return False, t("err_feedback_email_failed")


def record_item_history(db, item_id, business_id, change_type, quantity_change, resulting_quantity, occurred_at, user_id, performed_by_name):
    db.execute(
        """
        INSERT INTO item_history (id, business_id, item_id, change_type, quantity_change, resulting_quantity,
                                   performed_by_user_id, performed_by_name, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (uuid.uuid4().hex, business_id, item_id, change_type, quantity_change, resulting_quantity, user_id, performed_by_name, occurred_at),
    )


def upload_photo_to_cloudinary(file_storage):
    if not CLOUDINARY_CLOUD_NAME or not CLOUDINARY_UPLOAD_PRESET:
        return None, t("err_photo_not_configured")

    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
    try:
        response = requests.post(
            url,
            files={"file": (file_storage.filename, file_storage.stream, file_storage.mimetype)},
            data={"upload_preset": CLOUDINARY_UPLOAD_PRESET},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("secure_url"), None
    except requests.RequestException:
        return None, t("err_photo_upload_failed")


def build_quantity_chart(history):
    if not history:
        return None

    width, height = 560, 220
    pad_left, pad_right, pad_top, pad_bottom = 34, 16, 20, 34
    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom

    quantities = [h["resulting_quantity"] for h in history]
    max_q = max(max(quantities), 1)
    n = len(history)

    def x_for(i):
        if n == 1:
            return pad_left + plot_width / 2
        return pad_left + (plot_width * i / (n - 1))

    def y_for(q):
        return pad_top + plot_height - (q / max_q * plot_height)

    points = []
    circles = []
    labels = []
    for i, h in enumerate(history):
        x = x_for(i)
        y = y_for(h["resulting_quantity"])
        points.append(f"{x:.1f},{y:.1f}")
        circles.append((x, y, h["resulting_quantity"]))
        try:
            label = datetime.fromisoformat(h["occurred_at"]).strftime("%b %d")
        except ValueError:
            label = h["occurred_at"]
        labels.append((x, label))

    svg = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" class="history-chart">']
    svg.append(
        f'<line x1="{pad_left}" y1="{pad_top + plot_height:.1f}" x2="{pad_left + plot_width:.1f}" '
        f'y2="{pad_top + plot_height:.1f}" stroke="#ccc" stroke-width="1" />'
    )
    svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#F2CB36" stroke-width="2" />')
    for x, y, q in circles:
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#F2CB36" />')
        svg.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" font-size="10" text-anchor="middle" fill="#333">{q}</text>')
    for x, label in labels:
        svg.append(
            f'<text x="{x:.1f}" y="{pad_top + plot_height + 16:.1f}" font-size="9" '
            f'text-anchor="middle" fill="#777">{label}</text>'
        )
    svg.append("</svg>")
    return "".join(svg)


def distinct_item_values(db, business_id, column):
    rows = db.execute(
        f"SELECT DISTINCT {column} AS value FROM items WHERE business_id = ? AND is_deleted = 0 "
        f"AND {column} IS NOT NULL AND {column} != ''",
        (business_id,),
    ).fetchall()
    seen = {}
    for row in rows:
        value = (row["value"] or "").strip()
        if value and value.lower() not in seen:
            seen[value.lower()] = value
    return sorted(seen.values(), key=str.lower)


SORTS = {
    "name_asc": lambda i: (i["item_name"] or "").lower(),
    "name_desc": lambda i: (i["item_name"] or "").lower(),
    "price_asc": lambda i: i["price_per_unit"],
    "price_desc": lambda i: -i["price_per_unit"],
    "qty_asc": lambda i: i["quantity"],
    "qty_desc": lambda i: -i["quantity"],
    "value_asc": lambda i: i["quantity"] * i["price_per_unit"],
    "value_desc": lambda i: -(i["quantity"] * i["price_per_unit"]),
}
SORTS_NEEDING_REVERSE = {"name_desc"}


def sale_range_bounds(range_param, start_date_param, end_date_param):
    now_dt = datetime.now()
    if range_param == "1h":
        return now_dt - timedelta(hours=1), None
    if range_param == "24h":
        return now_dt - timedelta(hours=24), None
    if range_param == "7d":
        return now_dt - timedelta(days=7), None
    if range_param == "30d":
        return now_dt - timedelta(days=30), None
    if range_param == "365d":
        return now_dt - timedelta(days=365), None
    if range_param == "custom":
        start = None
        end = None
        if start_date_param:
            try:
                start = datetime.combine(date.fromisoformat(start_date_param), datetime.min.time())
            except ValueError:
                start = None
        if end_date_param:
            try:
                end = datetime.combine(date.fromisoformat(end_date_param), datetime.max.time().replace(microsecond=0))
            except ValueError:
                end = None
        return start, end
    return None, None


def available_to_sell(item):
    return max(item["quantity"] - item["reserved_quantity"], 0)


app.jinja_env.globals["available_to_sell"] = available_to_sell

time_range_bounds = sale_range_bounds


def record_inventory_value_snapshot(db, business_id):
    total = db.execute(
        "SELECT COALESCE(SUM(quantity * price_per_unit), 0) AS total FROM items WHERE business_id = ? AND is_deleted = 0",
        (business_id,),
    ).fetchone()["total"]
    db.execute(
        "INSERT INTO inventory_value_history (id, business_id, total_value, recorded_at) VALUES (?, ?, ?, ?)",
        (uuid.uuid4().hex, business_id, total, datetime.now().isoformat(timespec="seconds")),
    )


def build_value_chart(snapshots):
    if not snapshots:
        return None

    width, height = 560, 220
    pad_left, pad_right, pad_top, pad_bottom = 46, 16, 20, 34
    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom

    values = [s["total_value"] for s in snapshots]
    max_v = max(max(values), 1)
    n = len(snapshots)

    def x_for(i):
        if n == 1:
            return pad_left + plot_width / 2
        return pad_left + (plot_width * i / (n - 1))

    def y_for(v):
        return pad_top + plot_height - (v / max_v * plot_height)

    points = []
    circles = []
    labels = []
    for i, s in enumerate(snapshots):
        x = x_for(i)
        y = y_for(s["total_value"])
        points.append(f"{x:.1f},{y:.1f}")
        circles.append((x, y))
        try:
            label = datetime.fromisoformat(s["recorded_at"]).strftime("%b %d")
        except ValueError:
            label = s["recorded_at"]
        labels.append((x, label))

    svg = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" class="history-chart">']
    svg.append(
        f'<line x1="{pad_left}" y1="{pad_top + plot_height:.1f}" x2="{pad_left + plot_width:.1f}" '
        f'y2="{pad_top + plot_height:.1f}" stroke="#ccc" stroke-width="1" />'
    )
    svg.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#F2CB36" stroke-width="2" />')
    for x, y in circles:
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#F2CB36" />')
    svg.append(
        f'<text x="{pad_left - 6:.1f}" y="{pad_top + 4:.1f}" font-size="9" text-anchor="end" fill="#777">'
        f'{max_v:,.0f}</text>'
    )
    svg.append(
        f'<text x="{pad_left - 6:.1f}" y="{pad_top + plot_height:.1f}" font-size="9" text-anchor="end" fill="#777">0</text>'
    )
    # Thin out x-axis labels so they don't overlap on narrow screens.
    label_step = max(1, len(labels) // 6)
    for i, (x, label) in enumerate(labels):
        if i % label_step == 0 or i == len(labels) - 1:
            svg.append(
                f'<text x="{x:.1f}" y="{pad_top + plot_height + 16:.1f}" font-size="9" '
                f'text-anchor="middle" fill="#777">{label}</text>'
            )
    svg.append("</svg>")
    return "".join(svg)


LOW_STOCK_THRESHOLD = 5

# Curated per-business theme options. "bg" drives backgrounds (buttons, active
# nav, etc.) and may be a gradient; "solid" is always a flat color, used
# anywhere CSS requires one (text, borders, icons) since gradients aren't
# valid there. The Enzi wordmark itself never uses these — see style.css.
THEME_PRESETS = {
    "enzi_green": {"label": "Enzi Gold (default)", "bg": "#F2CB36", "solid": "#F2CB36"},
    "ocean_blue": {"label": "Ocean Blue", "bg": "#2563eb", "solid": "#2563eb"},
    "sunset_orange": {"label": "Sunset Orange", "bg": "#ea580c", "solid": "#ea580c"},
    "royal_purple": {"label": "Royal Purple", "bg": "#7c3aed", "solid": "#7c3aed"},
    "rose_pink": {"label": "Rose Pink", "bg": "#db2777", "solid": "#db2777"},
    "charcoal": {"label": "Charcoal", "bg": "#374151", "solid": "#374151"},
    "gold": {"label": "Amber", "bg": "#b45309", "solid": "#b45309"},
    "crimson_red": {"label": "Crimson Red", "bg": "#b91c1c", "solid": "#b91c1c"},
    "tanzania_black_gradient": {"label": "Tanzania Black", "bg": "linear-gradient(135deg, #4b5563, #000000)", "solid": "#111827"},
    "tanzania_green_gradient": {"label": "Tanzania Green", "bg": "linear-gradient(135deg, #2ecc59, #0e7a2c)", "solid": "#1eb53a"},
    "tanzania_yellow_gradient": {"label": "Tanzania Yellow", "bg": "linear-gradient(135deg, #ffe066, #d4a017)", "solid": "#ca9a04"},
    "tanzania_blue_gradient": {"label": "Tanzania Blue", "bg": "linear-gradient(135deg, #0057b8, #002a5c)", "solid": "#00397a"},
}
DEFAULT_THEME_PRESET = "enzi_green"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def get_theme(business):
    if business and business["theme_preset"] == "custom" and business["theme_custom_color"]:
        color = business["theme_custom_color"]
        return {"label": "Custom", "bg": color, "solid": color}
    key = (business["theme_preset"] if business else None) or DEFAULT_THEME_PRESET
    return THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_THEME_PRESET])


def _require_business():
    """Returns (business, user) for the logged-in session, or a redirect Response if not ready."""
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return None, None, redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not business or not user:
        session.clear()
        return None, None, redirect(url_for("enter_name"))

    if not business["default_location"] or not business["country"] or not business["whatsapp_number"]:
        return None, None, redirect(url_for("set_location"))

    return business, user, None


@app.route("/")
def index():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    business_id = business["id"]
    db = get_db()

    items = list(
        db.execute(
            "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY created_at DESC",
            (business_id,),
        ).fetchall()
    )
    grand_total = sum(item["quantity"] * item["price_per_unit"] for item in items)

    value_range_param = request.args.get("value_range", "all")
    value_start_param = request.args.get("value_start_date", "")
    value_end_param = request.args.get("value_end_date", "")
    value_start, value_end = time_range_bounds(value_range_param, value_start_param, value_end_param)

    all_snapshots = db.execute(
        "SELECT * FROM inventory_value_history WHERE business_id = ? ORDER BY recorded_at ASC", (business_id,)
    ).fetchall()
    value_snapshots = []
    for snap in all_snapshots:
        try:
            recorded_dt = datetime.fromisoformat(snap["recorded_at"])
        except ValueError:
            continue
        if value_start and recorded_dt < value_start:
            continue
        if value_end and recorded_dt > value_end:
            continue
        value_snapshots.append(snap)
    value_chart_svg = build_value_chart(value_snapshots)

    all_sales = db.execute("SELECT * FROM sales WHERE business_id = ? ORDER BY sold_at DESC", (business_id,)).fetchall()
    recent_sales = all_sales[:5]

    reservations_count = db.execute(
        "SELECT count(*) AS c FROM reservations WHERE business_id = ? AND status = 'active'", (business_id,)
    ).fetchone()["c"]

    incoming = db.execute(
        "SELECT expected_date FROM incoming_stock WHERE business_id = ?", (business_id,)
    ).fetchall()

    today_str = date.today().isoformat()
    low_stock_count = sum(1 for item in items if available_to_sell(item) <= LOW_STOCK_THRESHOLD)
    overdue_incoming_count = sum(1 for inc in incoming if inc["expected_date"] and inc["expected_date"] < today_str)
    low_stock_dismissed = session.get("low_stock_dismissed", False)

    return render_template(
        "home.html",
        business=business,
        user=user,
        items_in_stock_count=sum(item["quantity"] for item in items),
        distinct_items_count=len(items),
        items_sold_count=sum(sale["quantity_sold"] for sale in all_sales),
        grand_total=grand_total,
        recent_sales=recent_sales,
        value_chart_svg=value_chart_svg,
        value_range=value_range_param,
        value_start_date=value_start_param,
        value_end_date=value_end_param,
        low_stock_count=low_stock_count,
        pending_reservations_count=reservations_count,
        overdue_incoming_count=overdue_incoming_count,
        low_stock_dismissed=low_stock_dismissed,
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        active_tab="home",
        just_added=session.pop("just_added", None),
        just_sold=session.pop("just_sold", None),
        error=session.pop("error", None),
    )


@app.route("/sales")
def sales_page():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    business_id = business["id"]
    db = get_db()

    range_param = request.args.get("range", "all")
    start_date_param = request.args.get("start_date", "")
    end_date_param = request.args.get("end_date", "")
    range_start, range_end = sale_range_bounds(range_param, start_date_param, end_date_param)

    all_sales = db.execute("SELECT * FROM sales WHERE business_id = ? ORDER BY sold_at DESC", (business_id,)).fetchall()
    sales = []
    for sale in all_sales:
        try:
            sold_dt = datetime.fromisoformat(sale["sold_at"])
        except ValueError:
            continue
        if range_start and sold_dt < range_start:
            continue
        if range_end and sold_dt > range_end:
            continue
        sales.append(sale)

    return render_template(
        "sales.html",
        business=business,
        user=user,
        sales=sales,
        range=range_param,
        start_date=start_date_param,
        end_date=end_date_param,
        active_tab="home",
    )


@app.route("/items")
def items_tab():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    business_id = business["id"]
    db = get_db()

    reservations = db.execute(
        "SELECT r.*, i.price_per_unit AS item_price_per_unit, i.quantity AS item_quantity "
        "FROM reservations r JOIN items i ON i.id = r.item_id "
        "WHERE r.business_id = ? AND r.status = 'active' ORDER BY r.reserved_at DESC",
        (business_id,),
    ).fetchall()

    deleted_rows = db.execute(
        "SELECT * FROM items WHERE business_id = ? AND is_deleted = 1 ORDER BY deleted_at DESC",
        (business_id,),
    ).fetchall()
    removal_rows = db.execute(
        "SELECT * FROM removals WHERE business_id = ? ORDER BY removed_at DESC", (business_id,)
    ).fetchall()
    deleted_entries = []
    for d in deleted_rows:
        deleted_entries.append({
            "kind": "deleted", "item_name": d["item_name"], "quantity": d["quantity"],
            "reason": d["deleted_reason"], "note": None, "by_name": d["deleted_by_name"], "at": d["deleted_at"],
        })
    for r in removal_rows:
        deleted_entries.append({
            "kind": "removed", "item_name": r["item_name"], "quantity": r["quantity"],
            "reason": r["reason"], "note": r["note"], "by_name": r["removed_by_name"], "at": r["removed_at"],
        })
    deleted_entries.sort(key=lambda e: e["at"] or "", reverse=True)

    incoming = db.execute(
        "SELECT * FROM incoming_stock WHERE business_id = ? ORDER BY created_at DESC",
        (business_id,),
    ).fetchall()

    restocks_raw = db.execute(
        "SELECT * FROM restocks WHERE business_id = ? ORDER BY created_at DESC", (business_id,)
    ).fetchall()
    item_vendor_by_id = {
        item["id"]: item["vendor_id"] for item in db.execute(
            "SELECT id, vendor_id FROM items WHERE business_id = ? AND vendor_id IS NOT NULL", (business_id,)
        ).fetchall()
    }
    vendor_names_by_id = {
        v["id"]: v["name"] for v in db.execute("SELECT id, name FROM vendors WHERE business_id = ?", (business_id,)).fetchall()
    }
    restocks = []
    for req in restocks_raw:
        req = dict(req)
        vendor_id = item_vendor_by_id.get(req["item_id"])
        req["suggested_vendor_name"] = vendor_names_by_id.get(vendor_id) if vendor_id else None
        restocks.append(req)

    wishlist = db.execute(
        "SELECT * FROM wishlist WHERE business_id = ? ORDER BY created_at DESC", (business_id,)
    ).fetchall()

    today_str = date.today().isoformat()

    return render_template(
        "items.html",
        business=business,
        user=user,
        reservations=reservations,
        deleted_entries=deleted_entries,
        incoming=incoming,
        restocks=restocks,
        wishlist=wishlist,
        today=today_str,
        active_tab="items",
        just_added=session.pop("just_added", None),
        just_sold=session.pop("just_sold", None),
        error=session.pop("error", None),
    )


@app.route("/items/add-new")
def add_new_item_page():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    business_id = business["id"]
    db = get_db()

    existing_items = db.execute(
        "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY item_name ASC", (business_id,)
    ).fetchall()
    items_data = {
        item["id"]: {
            "item_name": item["item_name"],
            "item_brand": item["item_brand"] or "",
            "item_type": item["item_type"] or "",
            "item_color": item["item_color"] or "",
            "item_size": item["item_size"] or "",
            "item_code": item["item_code"] or "",
            "location": item["location"] or "",
            "price_per_unit": item["price_per_unit"],
            "vendor_id": item["vendor_id"] or "",
            "notes": item["notes"] or "",
        }
        for item in existing_items
    }

    locations = distinct_item_values(db, business_id, "location")
    if business["default_location"] and business["default_location"] not in locations:
        locations = sorted(locations + [business["default_location"]], key=str.lower)

    vendors = db.execute("SELECT * FROM vendors WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall()

    return render_template(
        "add_new_item.html",
        business=business, user=user,
        existing_items=existing_items,
        items_data=items_data,
        vendors=vendors,
        locations=locations,
        item_names=distinct_item_values(db, business_id, "item_name"),
        item_types=distinct_item_values(db, business_id, "item_type"),
        item_colors=distinct_item_values(db, business_id, "item_color"),
        item_brands=distinct_item_values(db, business_id, "item_brand"),
        item_sizes=distinct_item_values(db, business_id, "item_size"),
        default_location=business["default_location"],
        active_tab="items",
    )


@app.route("/sell")
def sell_tab():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    business_id = business["id"]
    db = get_db()

    items = list(
        db.execute(
            "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY created_at DESC",
            (business_id,),
        ).fetchall()
    )

    q = request.args.get("q", "").strip()
    if q:
        q_lower = q.lower()
        def matches(item):
            fields = [item["item_name"], item["item_brand"], item["item_type"], item["item_color"], item["item_code"]]
            return any(f and q_lower in f.lower() for f in fields)
        items = [item for item in items if matches(item)]

    sort = request.args.get("sort", "name_asc")
    if sort in SORTS:
        items.sort(key=SORTS[sort], reverse=sort in SORTS_NEEDING_REVERSE)

    view = request.args.get("view", "grid")
    if view not in ("grid", "list"):
        view = "grid"

    return render_template(
        "sell.html", business=business, user=user, items=items, q=q, sort=sort, view=view,
        low_stock_threshold=LOW_STOCK_THRESHOLD, active_tab="sell",
        just_added=session.pop("just_added", None),
        just_sold=session.pop("just_sold", None),
        error=session.pop("error", None),
    )


@app.route("/vendors")
def vendors_tab():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()
    vendors = db.execute(
        "SELECT * FROM vendors WHERE business_id = ? ORDER BY name ASC", (business["id"],)
    ).fetchall()
    return render_template(
        "vendors.html", business=business, user=user, vendors=vendors, active_tab="vendors",
        just_added=session.pop("just_added", None), error=session.pop("error", None),
    )


@app.route("/vendors/add", methods=["POST"])
def add_vendor():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()

    name = _clamp_text(request.form.get("name"), 150)
    contact_phone = _clamp_text(request.form.get("contact_phone"), 30)
    contact_whatsapp = _clamp_text(request.form.get("contact_whatsapp"), 30)
    contact_email = _clamp_text(request.form.get("contact_email"), 150)
    contact_instagram = _clamp_text(request.form.get("contact_instagram"), 60)
    typically_supplies = _clamp_text(request.form.get("typically_supplies"), 1000)

    if not name:
        session["error"] = t("err_vendor_name_required")
        return redirect(url_for("vendors_tab"))
    if not _valid_phone(contact_phone) or not _valid_phone(contact_whatsapp):
        session["error"] = t("err_phone_invalid")
        return redirect(url_for("vendors_tab"))
    if not _valid_email(contact_email):
        session["error"] = t("err_email_invalid")
        return redirect(url_for("vendors_tab"))

    performed_by_name = f"{user['first_name']} {user['last_name']}"
    vendor_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO vendors (id, business_id, name, contact_phone, contact_whatsapp, contact_email, contact_instagram,
                              typically_supplies, created_by_user_id, created_by_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (vendor_id, business["id"], name, contact_phone or None, contact_whatsapp or None, contact_email or None,
         contact_instagram or None, typically_supplies or None, user["id"], performed_by_name, now),
    )
    db.commit()

    session["just_added"] = t("msg_vendor_added", name=name)
    return redirect(url_for("vendors_tab"))


@app.route("/vendors/<vendor_id>")
def vendor_detail(vendor_id):
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE id = ? AND business_id = ?", (vendor_id, business["id"])
    ).fetchone()
    if not vendor:
        return redirect(url_for("vendors_tab"))

    sourced_items = db.execute(
        "SELECT * FROM items WHERE business_id = ? AND vendor_id = ? AND is_deleted = 0 ORDER BY item_name ASC",
        (business["id"], vendor_id),
    ).fetchall()
    sourced_incoming = db.execute(
        "SELECT * FROM incoming_stock WHERE business_id = ? AND vendor_id = ? ORDER BY created_at DESC",
        (business["id"], vendor_id),
    ).fetchall()

    return render_template(
        "vendor_detail.html", business=business, user=user, vendor=vendor,
        sourced_items=sourced_items, sourced_incoming=sourced_incoming, active_tab="vendors",
    )


@app.route("/vendors/<vendor_id>/edit", methods=["GET", "POST"])
def edit_vendor(vendor_id):
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()
    vendor = db.execute(
        "SELECT * FROM vendors WHERE id = ? AND business_id = ?", (vendor_id, business["id"])
    ).fetchone()
    if not vendor:
        return redirect(url_for("vendors_tab"))

    if request.method == "POST":
        name = _clamp_text(request.form.get("name"), 150)
        contact_phone = _clamp_text(request.form.get("contact_phone"), 30)
        contact_whatsapp = _clamp_text(request.form.get("contact_whatsapp"), 30)
        contact_email = _clamp_text(request.form.get("contact_email"), 150)
        contact_instagram = _clamp_text(request.form.get("contact_instagram"), 60)
        typically_supplies = _clamp_text(request.form.get("typically_supplies"), 1000)

        if not name:
            return render_template("vendor_edit.html", vendor=vendor, error=t("err_vendor_name_required"), active_tab="vendors")
        if not _valid_phone(contact_phone) or not _valid_phone(contact_whatsapp):
            return render_template("vendor_edit.html", vendor=vendor, error=t("err_phone_invalid"), active_tab="vendors")
        if not _valid_email(contact_email):
            return render_template("vendor_edit.html", vendor=vendor, error=t("err_email_invalid"), active_tab="vendors")

        db.execute(
            """
            UPDATE vendors SET name = ?, contact_phone = ?, contact_whatsapp = ?, contact_email = ?,
                                contact_instagram = ?, typically_supplies = ?
            WHERE id = ? AND business_id = ?
            """,
            (name, contact_phone or None, contact_whatsapp or None, contact_email or None,
             contact_instagram or None, typically_supplies or None, vendor_id, business["id"]),
        )
        db.commit()
        return redirect(url_for("vendor_detail", vendor_id=vendor_id))

    return render_template("vendor_edit.html", vendor=vendor, error=None, active_tab="vendors")


@app.route("/customers")
def customers_tab():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()
    customers = db.execute(
        "SELECT * FROM customers WHERE business_id = ? ORDER BY name ASC", (business["id"],)
    ).fetchall()
    sale_counts = {
        row["customer_id"]: row["c"]
        for row in db.execute(
            "SELECT customer_id, COUNT(*) AS c FROM sales WHERE business_id = ? AND customer_id IS NOT NULL "
            "GROUP BY customer_id",
            (business["id"],),
        ).fetchall()
    }
    return render_template(
        "customers.html", business=business, user=user, customers=customers, sale_counts=sale_counts,
        active_tab="customers", just_added=session.pop("just_added", None), error=session.pop("error", None),
    )


@app.route("/customers/add", methods=["POST"])
def add_customer():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()

    name = _clamp_text(request.form.get("name"), 150)
    whatsapp_number = _clamp_text(request.form.get("whatsapp_number"), 30)
    delivery_details = _clamp_text(request.form.get("delivery_details"), 1000)
    notes = _clamp_text(request.form.get("notes"), 1000)

    if not name:
        session["error"] = t("err_customer_name_required")
        return redirect(url_for("customers_tab"))
    if not _valid_phone(whatsapp_number):
        session["error"] = t("err_phone_invalid")
        return redirect(url_for("customers_tab"))

    performed_by_name = f"{user['first_name']} {user['last_name']}"
    customer_id, resolved_name, resolve_error = _resolve_customer(
        db, business["id"], user["id"], performed_by_name, "", name, whatsapp_number
    )
    if resolve_error:
        session["error"] = resolve_error
        return redirect(url_for("customers_tab"))
    db.execute(
        "UPDATE customers SET delivery_details = ?, notes = ? WHERE id = ?",
        (delivery_details or None, notes or None, customer_id),
    )
    db.commit()

    session["just_added"] = t("msg_customer_added", name=resolved_name)
    return redirect(url_for("customers_tab"))


@app.route("/customers/<customer_id>")
def customer_detail(customer_id):
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()
    customer = db.execute(
        "SELECT * FROM customers WHERE id = ? AND business_id = ?", (customer_id, business["id"])
    ).fetchone()
    if not customer:
        return redirect(url_for("customers_tab"))

    sales = db.execute(
        "SELECT * FROM sales WHERE business_id = ? AND customer_id = ? ORDER BY sold_at DESC",
        (business["id"], customer_id),
    ).fetchall()
    active_reservations = db.execute(
        "SELECT * FROM reservations WHERE business_id = ? AND customer_id = ? AND status = 'active' "
        "ORDER BY reserved_at DESC",
        (business["id"], customer_id),
    ).fetchall()
    pending_restocks = db.execute(
        "SELECT * FROM restocks WHERE business_id = ? AND customer_id = ? ORDER BY created_at DESC",
        (business["id"], customer_id),
    ).fetchall()

    return render_template(
        "customer_detail.html", business=business, user=user, customer=customer, sales=sales,
        active_reservations=active_reservations, pending_restocks=pending_restocks, active_tab="customers",
    )


@app.route("/customers/<customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id):
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()
    customer = db.execute(
        "SELECT * FROM customers WHERE id = ? AND business_id = ?", (customer_id, business["id"])
    ).fetchone()
    if not customer:
        return redirect(url_for("customers_tab"))

    if request.method == "POST":
        name = _clamp_text(request.form.get("name"), 150)
        whatsapp_number = _clamp_text(request.form.get("whatsapp_number"), 30)
        delivery_details = _clamp_text(request.form.get("delivery_details"), 1000)
        notes = _clamp_text(request.form.get("notes"), 1000)

        if not name:
            return render_template(
                "customer_edit.html", customer=customer, error=t("err_customer_name_required"), active_tab="customers",
            )
        if not _valid_phone(whatsapp_number):
            return render_template(
                "customer_edit.html", customer=customer, error=t("err_phone_invalid"), active_tab="customers",
            )

        customer_key = name.lower()
        collision = db.execute(
            "SELECT id FROM customers WHERE business_id = ? AND customer_key = ? AND id != ?",
            (business["id"], customer_key, customer_id),
        ).fetchone()
        if collision:
            return render_template(
                "customer_edit.html", customer=customer, error=t("err_customer_name_collision", name=name),
                active_tab="customers",
            )

        db.execute(
            "UPDATE customers SET name = ?, customer_key = ?, whatsapp_number = ?, delivery_details = ?, notes = ? "
            "WHERE id = ? AND business_id = ?",
            (name, customer_key, whatsapp_number or None, delivery_details or None, notes or None,
             customer_id, business["id"]),
        )
        db.commit()
        return redirect(url_for("customer_detail", customer_id=customer_id))

    return render_template("customer_edit.html", customer=customer, error=None, active_tab="customers")


def _resolve_vendor(db, business_id, user_id, performed_by_name, vendor_id, new_vendor_name,
                     new_vendor_phone=None, new_vendor_email=None):
    """Returns (vendor_id, error). vendor_id is the one given, a newly created
    vendor if a name was typed for a new one, or None if neither is present."""
    vendor_id = (vendor_id or "").strip()
    new_vendor_name = _clamp_text(new_vendor_name, 150)
    new_vendor_phone = _clamp_text(new_vendor_phone, 30)
    new_vendor_email = _clamp_text(new_vendor_email, 150)
    if vendor_id and vendor_id != "__new__":
        return vendor_id, None
    if new_vendor_name:
        if not _valid_phone(new_vendor_phone):
            return None, t("err_phone_invalid")
        if not _valid_email(new_vendor_email):
            return None, t("err_email_invalid")
        new_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            "INSERT INTO vendors (id, business_id, name, contact_phone, contact_email, "
            "created_by_user_id, created_by_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id, business_id, new_vendor_name, new_vendor_phone or None, new_vendor_email or None,
             user_id, performed_by_name, now),
        )
        return new_id, None
    return None, None


def _resolve_customer(db, business_id, user_id, performed_by_name, customer_id, new_customer_name, new_customer_whatsapp=None):
    """Returns (customer_id, customer_name, error). Looks up an existing customer
    by id, or upserts a new one by (business_id, customer_key) dedup, or
    (None, None, None) if neither an id nor a new name was given — a customer
    is optional, so that's not itself an error."""
    customer_id = (customer_id or "").strip()
    new_customer_name = _clamp_text(new_customer_name, 150)
    new_customer_whatsapp = _clamp_text(new_customer_whatsapp, 30)

    if customer_id and customer_id != "__new__":
        row = db.execute(
            "SELECT id, name FROM customers WHERE id = ? AND business_id = ?", (customer_id, business_id)
        ).fetchone()
        if row:
            return row["id"], row["name"], None
        return None, None, None

    if new_customer_name:
        if not _valid_phone(new_customer_whatsapp):
            return None, None, t("err_phone_invalid")
        customer_key = new_customer_name.lower()
        new_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        cur = db.execute(
            """
            INSERT INTO customers (id, business_id, name, customer_key, whatsapp_number,
                                    created_by_user_id, created_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (business_id, customer_key) DO NOTHING
            RETURNING id, name
            """,
            (new_id, business_id, new_customer_name, customer_key, new_customer_whatsapp or None,
             user_id, performed_by_name, now),
        )
        row = cur.fetchone()
        if row:
            return row["id"], row["name"], None
        existing = db.execute(
            "SELECT id, name FROM customers WHERE business_id = ? AND customer_key = ?",
            (business_id, customer_key),
        ).fetchone()
        return existing["id"], existing["name"], None

    return None, None, None


@app.route("/enter-name", methods=["GET", "POST"])
def enter_name():
    if request.method == "POST":
        business_name = _clamp_text(request.form.get("business_name"), 150)
        first_name = _clamp_text(request.form.get("first_name"), 150)
        last_name = _clamp_text(request.form.get("last_name"), 150)
        pin = request.form.get("pin", "").strip()[:20]

        prefill = {"business_name": business_name, "first_name": first_name, "last_name": last_name}

        if not business_name or not first_name or not last_name or not pin:
            return render_template("enter_name.html", error=t("err_business_name_required"), **prefill)
        if len(pin) < 4:
            return render_template("enter_name.html", error=t("err_pin_too_short"), **prefill)

        db = get_db()
        business_key = business_name.lower()
        business = db.execute("SELECT * FROM businesses WHERE business_key = ?", (business_key,)).fetchone()

        if business:
            business_id = business["id"]
            if business["pin_hash"]:
                lockout_error = check_business_lockout(business)
                if lockout_error:
                    return render_template("enter_name.html", error=lockout_error, **prefill)
                if not check_password_hash(business["pin_hash"], pin):
                    record_failed_login(db, business)
                    return render_template("enter_name.html", error=t("err_pin_incorrect"), **prefill)
            else:
                # Grandfathered business created before PINs existed: the first
                # correct-looking attempt sets it going forward.
                db.execute(
                    "UPDATE businesses SET pin_hash = ? WHERE id = ?",
                    (generate_password_hash(pin), business_id),
                )
        else:
            business_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO businesses (id, business_key, business_name, default_location, country, region, "
                "pin_hash, account_number, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, NULL, ?, "
                "lpad(nextval('business_account_number_seq')::text, 9, '0'), ?)",
                (business_id, business_key, business_name, generate_password_hash(pin), datetime.now().isoformat(timespec="seconds")),
            )

        is_new_business = business is None

        user_key = make_user_key(first_name, last_name)
        user = db.execute("SELECT * FROM users WHERE business_id = ? AND user_key = ?", (business_id, user_key)).fetchone()
        now = datetime.now().isoformat(timespec="seconds")

        if user:
            user_id = user["id"]
            db.execute(
                "UPDATE users SET first_name = ?, last_name = ?, last_login_at = ? WHERE id = ?",
                (first_name, last_name, now, user_id),
            )
            db.commit()
        elif is_new_business:
            # The very first person to create a business has no admin yet to
            # add them to the roster, so they're auto-added as its founder.
            user_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO users (id, business_id, user_key, first_name, last_name, last_login_at, language, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, business_id, user_key, first_name, last_name, now, "en", now),
            )
            db.commit()
        else:
            # Existing business, but this name isn't on its approved roster.
            return render_template("enter_name.html", error=t("err_user_not_on_roster"), **prefill)

        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        device_token = request.cookies.get("enzi_device")
        if user["pin_hash"] and device_token and device_token == user["trusted_device_token"]:
            # Recognized device, already-verified identity: straight in.
            clear_failed_login(db, business_id)
            session["business_id"] = business_id
            session["user_id"] = user_id
            session.pop("low_stock_dismissed", None)
            return redirect(url_for("index"))

        # Unrecognized device, or this person has never set a personal PIN yet:
        # verify identity before completing login.
        session["pending_business_id"] = business_id
        session["pending_user_id"] = user_id
        return redirect(url_for("verify_identity"))

    return render_template("enter_name.html", error=None)


@app.route("/verify-identity", methods=["GET", "POST"])
def verify_identity():
    business_id = session.get("pending_business_id")
    user_id = session.get("pending_user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id = ? AND business_id = ?", (user_id, business_id)).fetchone()
    if not business or not user:
        session.pop("pending_business_id", None)
        session.pop("pending_user_id", None)
        return redirect(url_for("enter_name"))

    is_first_time = not user["pin_hash"]

    if request.method == "POST":
        pin = request.form.get("pin", "").strip()[:20]

        if not is_first_time:
            lockout_error = check_business_lockout(business)
            if lockout_error:
                return render_template("verify_identity.html", user=user, is_first_time=is_first_time, error=lockout_error)

        if len(pin) < 4:
            return render_template("verify_identity.html", user=user, is_first_time=is_first_time, error=t("err_pin_too_short"))

        if is_first_time:
            db.execute("UPDATE users SET pin_hash = ? WHERE id = ?", (generate_password_hash(pin), user_id))
        elif not check_password_hash(user["pin_hash"], pin):
            record_failed_login(db, business)
            return render_template("verify_identity.html", user=user, is_first_time=is_first_time, error=t("err_pin_incorrect"))

        device_token = uuid.uuid4().hex
        db.execute("UPDATE users SET trusted_device_token = ? WHERE id = ?", (device_token, user_id))
        clear_failed_login(db, business_id)
        db.commit()

        session.pop("pending_business_id", None)
        session.pop("pending_user_id", None)
        session["business_id"] = business_id
        session["user_id"] = user_id
        session.pop("low_stock_dismissed", None)

        response = redirect(url_for("index"))
        response.set_cookie("enzi_device", device_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
        return response

    return render_template("verify_identity.html", user=user, is_first_time=is_first_time, error=None)


@app.route("/set-location", methods=["GET", "POST"])
def set_location():
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not business:
        session.clear()
        return redirect(url_for("enter_name"))

    if business["default_location"] and business["country"] and business["whatsapp_number"]:
        return redirect(url_for("index"))

    if request.method == "POST":
        location = request.form.get("location", "").strip()
        country = request.form.get("country", "").strip()
        region = request.form.get("region", "").strip()
        whatsapp_number = _clamp_text(request.form.get("whatsapp_number"), 30)

        if not location or not country or not region or not whatsapp_number:
            return render_template(
                "set_location.html", error=t("err_set_location_required"),
                business=business, countries=COUNTRIES, tanzania_regions=TANZANIA_REGIONS,
            )
        if not _valid_phone(whatsapp_number):
            return render_template(
                "set_location.html", error=t("err_phone_invalid"),
                business=business, countries=COUNTRIES, tanzania_regions=TANZANIA_REGIONS,
            )

        db.execute(
            "UPDATE businesses SET default_location = ?, country = ?, region = ?, whatsapp_number = ? WHERE id = ?",
            (location, country, region, whatsapp_number, business_id),
        )
        db.commit()
        return redirect(url_for("index"))

    return render_template(
        "set_location.html", error=None, business=business, countries=COUNTRIES, tanzania_regions=TANZANIA_REGIONS,
    )


@app.route("/switch-user")
def switch_user():
    session.pop("business_id", None)
    session.pop("user_id", None)
    session.pop("low_stock_dismissed", None)
    return redirect(url_for("enter_name"))


@app.route("/dismiss-low-stock-banner", methods=["POST"])
def dismiss_low_stock_banner():
    session["low_stock_dismissed"] = True
    return redirect(url_for("index"))


@app.route("/admin")
def admin_chooser():
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))
    return render_template("admin_chooser.html")


@app.route("/admin/business")
def business_admin():
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not business:
        session.clear()
        return redirect(url_for("enter_name"))

    users = db.execute(
        "SELECT * FROM users WHERE business_id = ? ORDER BY created_at ASC", (business_id,)
    ).fetchall()

    return render_template(
        "admin.html", business=business, users=users,
        theme_presets=THEME_PRESETS, current_theme_key=business["theme_preset"] or DEFAULT_THEME_PRESET,
        store_url=url_for("store_seller", business_id=business_id, _external=True),
        marketplace_url=url_for("store_marketplace", _external=True),
        just_added=session.pop("just_added", None), error=session.pop("error", None),
    )


@app.route("/admin/add-user", methods=["POST"])
def add_team_member():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()

    first_name = _clamp_text(request.form.get("first_name"), 150)
    last_name = _clamp_text(request.form.get("last_name"), 150)
    if not first_name or not last_name:
        session["error"] = t("err_team_member_name_required")
        return redirect(url_for("business_admin"))

    user_key = make_user_key(first_name, last_name)
    existing = db.execute(
        "SELECT id FROM users WHERE business_id = ? AND user_key = ?", (business["id"], user_key)
    ).fetchone()
    if existing:
        session["error"] = t("err_team_member_exists")
        return redirect(url_for("business_admin"))

    performed_by_name = f"{user['first_name']} {user['last_name']}"
    new_id = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO users (id, business_id, user_key, first_name, last_name, language, created_at,
                            added_by_user_id, added_by_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id, business["id"], user_key, first_name, last_name, "en", now, user["id"], performed_by_name),
    )
    db.commit()

    session["just_added"] = t("msg_team_member_added", name=f"{first_name} {last_name}")
    return redirect(url_for("business_admin"))


@app.route("/admin/branding", methods=["POST"])
def update_branding():
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not business:
        session.clear()
        return redirect(url_for("enter_name"))

    theme_preset = request.form.get("theme_preset", "").strip()
    custom_color = request.form.get("custom_color", "").strip()
    theme_custom_color = business["theme_custom_color"]

    if theme_preset == "custom":
        if not HEX_COLOR_RE.match(custom_color):
            session["error"] = t("err_invalid_color")
            return redirect(url_for("business_admin"))
        theme_custom_color = custom_color
    elif theme_preset not in THEME_PRESETS:
        theme_preset = business["theme_preset"] or DEFAULT_THEME_PRESET

    logo_url = business["logo_url"]
    logo_file = request.files.get("logo")
    if logo_file and logo_file.filename:
        uploaded_url, logo_error = upload_photo_to_cloudinary(logo_file)
        if uploaded_url:
            logo_url = uploaded_url
        elif logo_error:
            session["error"] = logo_error

    db.execute(
        "UPDATE businesses SET theme_preset = ?, theme_custom_color = ?, logo_url = ? WHERE id = ?",
        (theme_preset, theme_custom_color, logo_url, business_id),
    )
    db.commit()

    session["just_added"] = t("msg_branding_updated")
    return redirect(url_for("business_admin"))


@app.route("/admin/store-settings", methods=["POST"])
def update_store_settings():
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not business:
        session.clear()
        return redirect(url_for("enter_name"))

    whatsapp_number = _clamp_text(request.form.get("whatsapp_number"), 30)
    if not whatsapp_number:
        session["error"] = t("err_business_whatsapp_required")
        return redirect(url_for("business_admin"))
    if not _valid_phone(whatsapp_number):
        session["error"] = t("err_phone_invalid")
        return redirect(url_for("business_admin"))

    db.execute("UPDATE businesses SET whatsapp_number = ? WHERE id = ?", (whatsapp_number, business_id))
    db.commit()

    session["just_added"] = t("msg_store_settings_updated")
    return redirect(url_for("business_admin"))


@app.route("/admin/personal", methods=["GET", "POST"])
def personal_admin():
    business, user, bounce = _require_business()
    if bounce:
        return bounce
    db = get_db()

    if request.method == "POST":
        contact_whatsapp = _clamp_text(request.form.get("contact_whatsapp"), 30)
        contact_email = _clamp_text(request.form.get("contact_email"), 150)

        if not _valid_phone(contact_whatsapp):
            return render_template(
                "personal_admin.html", user=user, error=t("err_phone_invalid"),
            )
        if not _valid_email(contact_email):
            return render_template(
                "personal_admin.html", user=user, error=t("err_email_invalid"),
            )

        db.execute(
            "UPDATE users SET contact_whatsapp = ?, contact_email = ? WHERE id = ?",
            (contact_whatsapp or None, contact_email or None, user["id"]),
        )
        db.commit()

        session["just_added"] = t("msg_personal_contact_updated")
        return redirect(url_for("personal_admin"))

    return render_template(
        "personal_admin.html", user=user, error=None,
        just_added=session.pop("just_added", None),
    )


def _add_quantity_to_item(db, item_id, business_id, user_id, performed_by_name, *,
                           quantity, price_per_unit, location, photo_url, vendor_id, item_name, default_location=None):
    now = datetime.now().isoformat(timespec="seconds")
    item = _get_owned_item(db, item_id, business_id)
    new_location = location or item["location"] or default_location
    new_photo_url = photo_url or item["photo_url"]
    new_vendor_id = vendor_id or item["vendor_id"]
    db.execute(
        """
        UPDATE items SET quantity = quantity + ?, price_per_unit = ?, location = ?, photo_url = ?,
                          last_added_by_user_id = ?, last_added_by_name = ?, updated_at = ?, vendor_id = ?
        WHERE id = ?
        """,
        (quantity, price_per_unit, new_location, new_photo_url, user_id, performed_by_name, now, new_vendor_id, item_id),
    )
    new_quantity = db.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()["quantity"]
    record_item_history(db, item_id, business_id, "added", quantity, new_quantity, now, user_id, performed_by_name)
    return t("msg_stock_increased", name=item_name, qty=new_quantity)


def _create_new_item(db, business_id, user_id, performed_by_name, default_location, *,
                      item_name, item_type, item_color, item_brand, item_size, item_code,
                      location, photo_url, quantity, price_per_unit, notes=None, vendor_id=None):
    now = datetime.now().isoformat(timespec="seconds")
    item_id = uuid.uuid4().hex
    new_location = location or default_location
    db.execute(
        """
        INSERT INTO items (id, business_id, item_name, item_type, item_color, item_brand, item_size, item_code, location, photo_url,
                            quantity, reserved_quantity, price_per_unit, created_by_user_id, created_by_name,
                            last_added_by_user_id, last_added_by_name, is_deleted, notes, vendor_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            item_id, business_id, item_name, item_type or None, item_color or None, item_brand or None, item_size or None,
            item_code or None, new_location, photo_url, quantity, price_per_unit, user_id, performed_by_name,
            user_id, performed_by_name, notes or None, vendor_id, now, now,
        ),
    )
    record_item_history(db, item_id, business_id, "added", quantity, quantity, now, user_id, performed_by_name)
    return t("msg_added_to_stock", name=item_name)


def apply_stock_addition(db, business_id, user_id, performed_by_name, default_location, *,
                          item_name, item_type, item_color, item_brand, item_size, item_code,
                          location, photo_url, quantity, price_per_unit, notes=None, vendor_id=None):
    existing_item = find_matching_item(db, business_id, item_name, item_type, item_color, item_brand, item_size, item_code)

    if existing_item:
        return _add_quantity_to_item(
            db, existing_item["id"], business_id, user_id, performed_by_name,
            quantity=quantity, price_per_unit=price_per_unit, location=location, photo_url=photo_url,
            vendor_id=vendor_id, item_name=item_name, default_location=default_location,
        )

    return _create_new_item(
        db, business_id, user_id, performed_by_name, default_location,
        item_name=item_name, item_type=item_type, item_color=item_color, item_brand=item_brand,
        item_size=item_size, item_code=item_code, location=location, photo_url=photo_url,
        quantity=quantity, price_per_unit=price_per_unit, notes=notes, vendor_id=vendor_id,
    )


@app.route("/add-item", methods=["POST"])
def add_item():
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    performed_by_name = f"{user['first_name']} {user['last_name']}" if user else "Unknown"

    item_name = _clamp_text(request.form.get("item_name"), 150)
    item_type = _clamp_text(request.form.get("item_type"), 150)
    item_color = _clamp_text(request.form.get("item_color"), 150)
    item_brand = _clamp_text(request.form.get("item_brand"), 150)
    item_size = _clamp_text(request.form.get("item_size"), 150)
    item_code = _clamp_text(request.form.get("item_code"), 150)
    location = _clamp_text(request.form.get("location"), 150)
    quantity_raw = request.form.get("quantity", "").strip()
    price_raw = request.form.get("price_per_unit", "").strip()
    notes = _clamp_text(request.form.get("notes"), 1000)
    photo_file = request.files.get("photo")
    photo_url_carry = request.form.get("photo_url_carry", "").strip()
    vendor_id_raw = request.form.get("vendor_id", "").strip()
    new_vendor_name = _clamp_text(request.form.get("new_vendor_name"), 150)
    new_vendor_phone = _clamp_text(request.form.get("new_vendor_phone"), 30)
    new_vendor_email = _clamp_text(request.form.get("new_vendor_email"), 150)
    existing_item_id = request.form.get("existing_item_id", "").strip()
    confirm_action = request.form.get("confirm_action", "").strip()

    if not item_name or not quantity_raw or not price_raw:
        session["error"] = t("err_item_required_fields")
        return redirect(url_for("items_tab"))

    try:
        quantity = int(quantity_raw)
        price_per_unit = float(price_raw)
    except ValueError:
        session["error"] = t("err_quantity_price_numbers")
        return redirect(url_for("items_tab"))

    if quantity < 0:
        session["error"] = t("err_quantity_negative")
        return redirect(url_for("items_tab"))
    if quantity > 100000:
        session["error"] = t("err_quantity_too_large")
        return redirect(url_for("items_tab"))
    if price_per_unit < 0:
        session["error"] = t("err_price_negative")
        return redirect(url_for("items_tab"))
    if price_per_unit > 500000000:
        session["error"] = t("err_price_too_large")
        return redirect(url_for("items_tab"))

    photo_url = photo_url_carry or None
    photo_error = None
    if photo_file and photo_file.filename:
        photo_url, photo_error = upload_photo_to_cloudinary(photo_file)

    default_location = business["default_location"] if business else None

    # If the seller explicitly picked an existing item from the list, that's a
    # definite match -- no need to guess from name/type/color/etc. Only ask
    # for confirmation when the price they typed doesn't match what's on file,
    # since that's genuinely ambiguous (typo? real price change? different item
    # that happens to share a name?).
    picked_item = _get_owned_item(db, existing_item_id, business_id) if existing_item_id else None
    if picked_item and abs(picked_item["price_per_unit"] - price_per_unit) > 0.009 and confirm_action not in ("update_existing", "create_new"):
        return render_template(
            "confirm_item_price.html",
            existing_item=picked_item, new_price=price_per_unit, quantity=quantity,
            form_data=dict(
                item_name=item_name, item_type=item_type, item_color=item_color, item_brand=item_brand,
                item_size=item_size, item_code=item_code, location=location, quantity=quantity,
                price_per_unit=price_per_unit, notes=notes, vendor_id=vendor_id_raw,
                new_vendor_name=new_vendor_name, new_vendor_phone=new_vendor_phone, new_vendor_email=new_vendor_email,
                existing_item_id=existing_item_id, photo_url_carry=photo_url or "",
            ),
        )

    vendor_id, vendor_error = _resolve_vendor(
        db, business_id, user_id, performed_by_name, vendor_id_raw, new_vendor_name, new_vendor_phone, new_vendor_email
    )
    if vendor_error:
        session["error"] = vendor_error
        return redirect(url_for("items_tab"))

    if picked_item and confirm_action == "create_new":
        session["just_added"] = _create_new_item(
            db, business_id, user_id, performed_by_name, default_location,
            item_name=item_name, item_type=item_type, item_color=item_color, item_brand=item_brand,
            item_size=item_size, item_code=item_code, location=location, photo_url=photo_url,
            quantity=quantity, price_per_unit=price_per_unit, notes=notes, vendor_id=vendor_id,
        )
    elif picked_item:
        session["just_added"] = _add_quantity_to_item(
            db, picked_item["id"], business_id, user_id, performed_by_name,
            quantity=quantity, price_per_unit=price_per_unit, location=location, photo_url=photo_url,
            vendor_id=vendor_id, item_name=item_name, default_location=default_location,
        )
    else:
        session["just_added"] = apply_stock_addition(
            db, business_id, user_id, performed_by_name, default_location,
            item_name=item_name, item_type=item_type, item_color=item_color, item_brand=item_brand,
            item_size=item_size, item_code=item_code, location=location, photo_url=photo_url,
            quantity=quantity, price_per_unit=price_per_unit, notes=notes, vendor_id=vendor_id,
        )

    if photo_error:
        session["error"] = photo_error

    record_inventory_value_snapshot(db, business_id)
    db.commit()
    return redirect(url_for("items_tab"))


def _get_owned_item(db, item_id, business_id):
    return db.execute(
        "SELECT * FROM items WHERE id = ? AND business_id = ? AND is_deleted = 0", (item_id, business_id)
    ).fetchone()


def _current_user_name(db, user_id):
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return f"{user['first_name']} {user['last_name']}" if user else "Unknown"


@app.route("/items/<item_id>/sell", methods=["GET", "POST"])
def sell_item(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    performed_by_name = _current_user_name(db, user_id)
    available = available_to_sell(item)
    customers = db.execute("SELECT * FROM customers WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall()

    reservation_id = request.values.get("reservation_id", "").strip() or None
    reservation = None
    if reservation_id:
        reservation = db.execute(
            "SELECT * FROM reservations WHERE id = ? AND business_id = ? AND item_id = ? AND status = 'active'",
            (reservation_id, business_id, item_id),
        ).fetchone()
        if not reservation:
            reservation_id = None

    max_sell = min(available + reservation["quantity"], item["quantity"]) if reservation else available

    if request.method == "POST":
        quantity_sold_raw = request.form.get("quantity_sold", "").strip()
        sale_price_raw = request.form.get("sale_price", "").strip()
        customer_id_raw = request.form.get("customer_id", "").strip()
        new_customer_name = request.form.get("new_customer_name", "").strip()
        new_customer_whatsapp = request.form.get("new_customer_whatsapp", "").strip()
        sale_date_raw = request.form.get("sale_date", "").strip()

        resolved_customer_id, customer_name, resolve_error = _resolve_customer(
            db, business_id, user_id, performed_by_name, customer_id_raw, new_customer_name, new_customer_whatsapp
        )
        customer_name = customer_name or ""

        error = resolve_error
        quantity_sold = None
        sale_price = None
        try:
            quantity_sold = int(quantity_sold_raw)
            sale_price = float(sale_price_raw)
        except ValueError:
            error = error or t("err_sell_numbers")

        if error is None and quantity_sold <= 0:
            error = t("err_sell_qty_min")
        elif error is None and quantity_sold > max_sell:
            error = t("err_sell_qty_available", max=max_sell, qty=quantity_sold)
        elif error is None and quantity_sold > 100000:
            error = t("err_quantity_too_large")
        elif error is None and sale_price < 0:
            error = t("err_price_negative")
        elif error is None and sale_price > 500000000:
            error = t("err_price_too_large")

        sold_at = None
        if error is None and sale_date_raw:
            try:
                chosen_date = date.fromisoformat(sale_date_raw)
                sold_at = datetime.combine(chosen_date, datetime.now().time()).isoformat(timespec="seconds")
            except ValueError:
                error = t("err_sale_date_invalid")

        if error:
            return render_template(
                "sell_item.html", item=item, error=error, today=date.today().isoformat(),
                performed_by_name=performed_by_name, available=max_sell, reservation=reservation, customers=customers,
            )

        now = datetime.now().isoformat(timespec="seconds")
        if sold_at is None:
            sold_at = now

        # Atomic, race-safe stock decrement: only succeeds if enough stock still exists
        # at the moment of the write, regardless of what this request read earlier.
        cursor = db.execute(
            "UPDATE items SET quantity = quantity - ?, updated_at = ? WHERE id = ? AND quantity >= ?",
            (quantity_sold, now, item_id, quantity_sold),
        )
        if cursor.rowcount == 0:
            db.rollback()
            fresh_item = _get_owned_item(db, item_id, business_id) or item
            return render_template(
                "sell_item.html", item=fresh_item, error=t("err_stock_changed_retry"),
                today=date.today().isoformat(), performed_by_name=performed_by_name,
                available=available_to_sell(fresh_item), reservation=reservation, customers=customers,
            )
        new_quantity = db.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()["quantity"]

        sale_id = uuid.uuid4().hex
        db.execute(
            """
            INSERT INTO sales (id, business_id, item_id, item_name, quantity_sold, sale_price, customer_name,
                                customer_key, customer_id, sold_by_user_id, sold_by_name, sold_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sale_id, business_id, item_id, item["item_name"], quantity_sold, sale_price, customer_name,
                customer_name.strip().lower(), resolved_customer_id, user_id, performed_by_name, sold_at,
            ),
        )
        record_item_history(db, item_id, business_id, "sold", -quantity_sold, new_quantity, sold_at, user_id, performed_by_name)

        if reservation:
            db.execute(
                "UPDATE items SET reserved_quantity = GREATEST(reserved_quantity - ?, 0) WHERE id = ?",
                (reservation["quantity"], item_id),
            )
            db.execute("UPDATE reservations SET status = 'sold' WHERE id = ?", (reservation["id"],))

        record_inventory_value_snapshot(db, business_id)
        db.commit()

        session["just_sold"] = t("msg_sold", qty=quantity_sold, name=item["item_name"], customer=customer_name or t("walk_in_customer"))
        return redirect(url_for("index"))

    return render_template(
        "sell_item.html", item=item, error=None, today=date.today().isoformat(),
        performed_by_name=performed_by_name, available=max_sell, reservation=reservation, customers=customers,
    )


@app.route("/items/<item_id>/reserve", methods=["GET", "POST"])
def reserve_item(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    performed_by_name = _current_user_name(db, user_id)
    available = available_to_sell(item)
    customers = db.execute("SELECT * FROM customers WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall()

    if request.method == "POST":
        quantity_raw = request.form.get("quantity", "").strip()
        agreed_price_raw = request.form.get("agreed_price", "").strip()
        customer_id_raw = request.form.get("customer_id", "").strip()
        new_customer_name = request.form.get("new_customer_name", "").strip()
        new_customer_whatsapp = request.form.get("new_customer_whatsapp", "").strip()
        expected_sale_date = request.form.get("expected_sale_date", "").strip()

        resolved_customer_id, customer_name, resolve_error = _resolve_customer(
            db, business_id, user_id, performed_by_name, customer_id_raw, new_customer_name, new_customer_whatsapp
        )
        customer_name = customer_name or ""

        error = resolve_error
        quantity = None
        agreed_price = None
        try:
            quantity = int(quantity_raw)
            agreed_price = float(agreed_price_raw)
        except ValueError:
            error = error or t("err_reserve_numbers")

        if error is None and quantity <= 0:
            error = t("err_reserve_qty_min")
        elif error is None and quantity > available:
            error = t("err_reserve_qty_available", avail=available, qty=quantity)
        elif error is None and quantity > 100000:
            error = t("err_quantity_too_large")
        elif error is None and agreed_price < 0:
            error = t("err_price_negative")
        elif error is None and agreed_price > 500000000:
            error = t("err_price_too_large")

        if error:
            return render_template(
                "reserve_item.html", item=item, error=error, performed_by_name=performed_by_name,
                available=available, customers=customers,
            )

        now = datetime.now().isoformat(timespec="seconds")

        # Atomic, race-safe reservation guard: only succeeds if enough unreserved
        # stock still exists at the moment of the write, regardless of what this
        # request read earlier (mirrors sell_item's atomic decrement).
        cursor = db.execute(
            "UPDATE items SET reserved_quantity = reserved_quantity + ?, updated_at = ? "
            "WHERE id = ? AND (quantity - reserved_quantity) >= ?",
            (quantity, now, item_id, quantity),
        )
        if cursor.rowcount == 0:
            db.rollback()
            fresh_item = _get_owned_item(db, item_id, business_id) or item
            return render_template(
                "reserve_item.html", item=fresh_item, error=t("err_stock_changed_retry"),
                performed_by_name=performed_by_name, available=available_to_sell(fresh_item), customers=customers,
            )

        db.execute(
            """
            INSERT INTO reservations (id, business_id, item_id, item_name, quantity, agreed_price, customer_name,
                                       customer_key, customer_id, reserved_by_user_id, reserved_by_name,
                                       expected_sale_date, reserved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, business_id, item_id, item["item_name"], quantity, agreed_price, customer_name,
                customer_name.strip().lower(), resolved_customer_id, user_id, performed_by_name,
                expected_sale_date or None, now,
            ),
        )
        record_item_history(db, item_id, business_id, "reserved", quantity, item["quantity"], now, user_id, performed_by_name)
        db.commit()

        session["just_sold"] = t("msg_reserved", qty=quantity, name=item["item_name"], customer=customer_name or t("walk_in_customer"))
        return redirect(url_for("sell_tab"))

    return render_template(
        "reserve_item.html", item=item, error=None, performed_by_name=performed_by_name,
        available=available, customers=customers,
    )


@app.route("/items/<item_id>/remove", methods=["GET", "POST"])
def remove_item(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    performed_by_name = _current_user_name(db, user_id)

    if request.method == "POST":
        quantity_raw = request.form.get("quantity", "").strip()
        reason = request.form.get("reason", "").strip()
        note = _clamp_text(request.form.get("note"), 1000)

        error = None
        quantity = None
        try:
            quantity = int(quantity_raw)
        except ValueError:
            error = t("err_remove_qty_whole")

        if error is None and quantity <= 0:
            error = t("err_remove_qty_min")
        elif error is None and quantity > item["quantity"]:
            error = t("err_remove_qty_available", qty=item["quantity"], removeQty=quantity)
        elif error is None and reason not in REMOVAL_REASONS:
            error = t("err_remove_reason_required")

        if error:
            return render_template(
                "remove_item.html", item=item, error=error, performed_by_name=performed_by_name,
                removal_reasons=REMOVAL_REASONS,
            )

        now = datetime.now().isoformat(timespec="seconds")
        cursor = db.execute(
            "UPDATE items SET quantity = quantity - ?, updated_at = ? WHERE id = ? AND quantity >= ?",
            (quantity, now, item_id, quantity),
        )
        if cursor.rowcount == 0:
            db.rollback()
            fresh_item = _get_owned_item(db, item_id, business_id) or item
            return render_template(
                "remove_item.html", item=fresh_item, error=t("err_stock_changed_retry"),
                performed_by_name=performed_by_name, removal_reasons=REMOVAL_REASONS,
            )
        new_quantity = db.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()["quantity"]

        db.execute(
            """
            INSERT INTO removals (id, business_id, item_id, item_name, quantity, reason, note,
                                   removed_by_user_id, removed_by_name, removed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, business_id, item_id, item["item_name"], quantity, reason, note or None, user_id, performed_by_name, now),
        )
        record_item_history(db, item_id, business_id, "removed", -quantity, new_quantity, now, user_id, performed_by_name)
        record_inventory_value_snapshot(db, business_id)
        db.commit()

        session["just_sold"] = t("msg_removed", qty=quantity, name=item["item_name"], reason=reason)
        return redirect(url_for("sell_tab"))

    return render_template(
        "remove_item.html", item=item, error=None, performed_by_name=performed_by_name,
        removal_reasons=REMOVAL_REASONS,
    )


@app.route("/items/<item_id>/add-stock", methods=["GET", "POST"])
def add_stock(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    performed_by_name = _current_user_name(db, user_id)
    vendors = db.execute("SELECT * FROM vendors WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall()

    if request.method == "POST":
        quantity_raw = request.form.get("quantity", "").strip()
        price_raw = request.form.get("price_per_unit", "").strip()
        vendor_id_raw = request.form.get("vendor_id", "").strip()
        new_vendor_name = _clamp_text(request.form.get("new_vendor_name"), 150)
        new_vendor_phone = _clamp_text(request.form.get("new_vendor_phone"), 30)
        new_vendor_email = _clamp_text(request.form.get("new_vendor_email"), 150)

        error = None
        quantity = None
        try:
            quantity = int(quantity_raw)
        except ValueError:
            error = t("err_add_stock_qty_whole")

        price_per_unit = item["price_per_unit"]
        if error is None and price_raw:
            try:
                price_per_unit = float(price_raw)
            except ValueError:
                error = t("err_add_stock_price_number")

        if error is None and quantity <= 0:
            error = t("err_add_stock_qty_min")
        elif error is None and quantity > 100000:
            error = t("err_quantity_too_large")
        elif error is None and price_per_unit < 0:
            error = t("err_price_negative")
        elif error is None and price_per_unit > 500000000:
            error = t("err_price_too_large")

        if error:
            return render_template("add_stock.html", item=item, error=error, performed_by_name=performed_by_name, vendors=vendors)

        vendor_id, vendor_error = _resolve_vendor(
            db, business_id, user_id, performed_by_name, vendor_id_raw, new_vendor_name, new_vendor_phone, new_vendor_email
        )
        if vendor_error:
            return render_template("add_stock.html", item=item, error=vendor_error, performed_by_name=performed_by_name, vendors=vendors)

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            UPDATE items SET quantity = quantity + ?, price_per_unit = ?, last_added_by_user_id = ?, last_added_by_name = ?,
                              updated_at = ?, vendor_id = COALESCE(?, vendor_id)
            WHERE id = ?
            """,
            (quantity, price_per_unit, user_id, performed_by_name, now, vendor_id, item_id),
        )
        new_quantity = db.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()["quantity"]
        record_item_history(db, item_id, business_id, "added", quantity, new_quantity, now, user_id, performed_by_name)
        record_inventory_value_snapshot(db, business_id)
        db.commit()

        session["just_added"] = t("msg_stock_increased", name=item["item_name"], qty=new_quantity)
        return redirect(url_for("sell_tab"))

    return render_template("add_stock.html", item=item, error=None, performed_by_name=performed_by_name, vendors=vendors)


@app.route("/items/<item_id>/delete", methods=["GET", "POST"])
def delete_item(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    performed_by_name = _current_user_name(db, user_id)

    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        if not reason:
            return render_template("delete_item.html", item=item, error=t("err_delete_reason_required"), performed_by_name=performed_by_name)

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            UPDATE items SET is_deleted = 1, deleted_by_user_id = ?, deleted_by_name = ?, deleted_at = ?, deleted_reason = ?
            WHERE id = ?
            """,
            (user_id, performed_by_name, now, reason, item_id),
        )
        record_inventory_value_snapshot(db, business_id)
        db.commit()
        session["just_added"] = t("msg_deleted", name=item["item_name"])
        return redirect(url_for("sell_tab"))

    return render_template("delete_item.html", item=item, error=None, performed_by_name=performed_by_name)


@app.route("/reservations/<reservation_id>/cancel", methods=["POST"])
def cancel_reservation(reservation_id):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    reservation = db.execute(
        "SELECT * FROM reservations WHERE id = ? AND business_id = ? AND status = 'active'",
        (reservation_id, business_id),
    ).fetchone()
    if reservation:
        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            "UPDATE items SET reserved_quantity = GREATEST(reserved_quantity - ?, 0), updated_at = ? WHERE id = ?",
            (reservation["quantity"], now, reservation["item_id"]),
        )
        db.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ?", (reservation_id,))
        db.commit()
        session["just_sold"] = t("msg_cancelled_reservation", qty=reservation["quantity"], name=reservation["item_name"], customer=reservation["customer_name"])

    return redirect(url_for("items_tab"))


@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    performed_by_name = _current_user_name(db, user_id)
    business_name = business["business_name"] if business else "Unknown"

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        contact_email = request.form.get("contact_email", "").strip()
        contact_whatsapp = request.form.get("contact_whatsapp", "").strip()

        if not message:
            return render_template(
                "feedback.html", error=t("err_feedback_message_required"),
                business=business, business_name=business_name, user_name=performed_by_name,
                message=message, contact_email=contact_email, contact_whatsapp=contact_whatsapp,
            )
        if not contact_email and not contact_whatsapp:
            return render_template(
                "feedback.html", error=t("err_feedback_contact_required"),
                business=business, business_name=business_name, user_name=performed_by_name,
                message=message, contact_email=contact_email, contact_whatsapp=contact_whatsapp,
            )

        now = datetime.now().isoformat(timespec="seconds")
        subject = "DUKANI - FEEDBACK"
        contact_lines = []
        if contact_email:
            contact_lines.append(f"Contact email: {contact_email}")
        if contact_whatsapp:
            contact_lines.append(f"Contact WhatsApp: {contact_whatsapp}")
        body = (
            f"DUKANI - FEEDBACK\n"
            f"Business: {business_name}\n"
            f"User: {performed_by_name}\n"
            f"Timestamp: {format_datetime(now)}\n"
            + "\n".join(contact_lines) + "\n\n"
            f"{message}"
        )
        sent, send_error = send_feedback_email(subject, body)

        db.execute(
            """
            INSERT INTO feedback (id, business_id, business_name, user_id, user_name, message, email_sent,
                                   contact_email, contact_whatsapp, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, business_id, business_name, user_id, performed_by_name, message, 1 if sent else 0,
                contact_email or None, contact_whatsapp or None, now,
            ),
        )
        db.commit()

        session["just_added"] = t("msg_feedback_sent") if sent else send_error
        return redirect(url_for("index"))

    return render_template(
        "feedback.html", error=None, business=business, business_name=business_name, user_name=performed_by_name,
        message="", contact_email="", contact_whatsapp="",
    )


def _incoming_form_context(db, business_id, business, error, prefill=None):
    existing_items = db.execute(
        "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY item_name ASC", (business_id,)
    ).fetchall()
    items_data = {
        item["id"]: {
            "item_name": item["item_name"],
            "item_brand": item["item_brand"] or "",
            "item_type": item["item_type"] or "",
            "item_color": item["item_color"] or "",
            "item_size": item["item_size"] or "",
            "item_code": item["item_code"] or "",
            "location": item["location"] or "",
            "price_per_unit": item["price_per_unit"],
            "vendor_id": item["vendor_id"] or "",
        }
        for item in existing_items
    }
    return dict(
        error=error,
        prefill=prefill or {},
        existing_items=existing_items,
        items_data=items_data,
        item_names=distinct_item_values(db, business_id, "item_name"),
        item_types=distinct_item_values(db, business_id, "item_type"),
        item_colors=distinct_item_values(db, business_id, "item_color"),
        item_brands=distinct_item_values(db, business_id, "item_brand"),
        item_sizes=distinct_item_values(db, business_id, "item_size"),
        locations=distinct_item_values(db, business_id, "location"),
        default_location=business["default_location"] if business else "",
        vendors=db.execute("SELECT * FROM vendors WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall(),
    )


@app.route("/incoming/add", methods=["GET", "POST"])
def add_incoming():
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    performed_by_name = f"{user['first_name']} {user['last_name']}" if user else "Unknown"

    if request.method == "POST":
        item_name = _clamp_text(request.form.get("item_name"), 150)
        item_type = _clamp_text(request.form.get("item_type"), 150)
        item_color = _clamp_text(request.form.get("item_color"), 150)
        item_brand = _clamp_text(request.form.get("item_brand"), 150)
        item_size = _clamp_text(request.form.get("item_size"), 150)
        item_code = _clamp_text(request.form.get("item_code"), 150)
        location = _clamp_text(request.form.get("location"), 150)
        quantity_raw = request.form.get("quantity", "").strip()
        price_raw = request.form.get("price_per_unit", "").strip()
        expected_date = request.form.get("expected_date", "").strip()
        photo_file = request.files.get("photo")
        source_wishlist_id = request.form.get("source_wishlist_id", "").strip()
        source_restock_id = request.form.get("source_restock_id", "").strip()
        vendor_id_raw = request.form.get("vendor_id", "").strip()
        new_vendor_name = _clamp_text(request.form.get("new_vendor_name"), 150)
        new_vendor_phone = _clamp_text(request.form.get("new_vendor_phone"), 30)
        new_vendor_email = _clamp_text(request.form.get("new_vendor_email"), 150)

        if not item_name or not quantity_raw or not price_raw:
            return render_template(
                "incoming_add.html",
                **_incoming_form_context(db, business_id, business, t("err_incoming_required_fields"), request.form),
            )

        try:
            quantity = int(quantity_raw)
            price_per_unit = float(price_raw)
        except ValueError:
            return render_template(
                "incoming_add.html",
                **_incoming_form_context(db, business_id, business, t("err_incoming_numbers"), request.form),
            )

        if quantity < 0 or price_per_unit < 0:
            return render_template(
                "incoming_add.html",
                **_incoming_form_context(db, business_id, business, t("err_incoming_negative"), request.form),
            )
        if quantity > 100000:
            return render_template(
                "incoming_add.html",
                **_incoming_form_context(db, business_id, business, t("err_quantity_too_large"), request.form),
            )
        if price_per_unit > 500000000:
            return render_template(
                "incoming_add.html",
                **_incoming_form_context(db, business_id, business, t("err_price_too_large"), request.form),
            )

        photo_url = None
        photo_error = None
        if photo_file and photo_file.filename:
            photo_url, photo_error = upload_photo_to_cloudinary(photo_file)

        vendor_id, vendor_error = _resolve_vendor(
            db, business_id, user_id, performed_by_name, vendor_id_raw, new_vendor_name, new_vendor_phone, new_vendor_email
        )
        if vendor_error:
            return render_template(
                "incoming_add.html",
                **_incoming_form_context(db, business_id, business, vendor_error, request.form),
            )

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            INSERT INTO incoming_stock (id, business_id, item_name, item_type, item_color, item_brand, item_size, item_code,
                                         location, photo_url, quantity, price_per_unit, expected_date, vendor_id,
                                         added_by_user_id, added_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, business_id, item_name, item_type or None, item_color or None, item_brand or None,
                item_size or None, item_code or None, location or None, photo_url, quantity, price_per_unit,
                expected_date or None, vendor_id, user_id, performed_by_name, now,
            ),
        )

        if source_wishlist_id:
            db.execute("DELETE FROM wishlist WHERE id = ? AND business_id = ?", (source_wishlist_id, business_id))
        if source_restock_id:
            db.execute("DELETE FROM restocks WHERE id = ? AND business_id = ?", (source_restock_id, business_id))

        db.commit()

        session["just_added"] = t("msg_added_upcoming", name=item_name)
        if photo_error:
            session["error"] = photo_error
        return redirect(url_for("items_tab"))

    prefill = {
        "item_name": request.args.get("item_name", ""),
        "item_type": request.args.get("item_type", ""),
        "item_color": request.args.get("item_color", ""),
        "item_brand": request.args.get("item_brand", ""),
        "item_size": request.args.get("item_size", ""),
        "item_code": request.args.get("item_code", ""),
        "location": request.args.get("location", ""),
        "quantity": request.args.get("quantity", ""),
        "price_per_unit": request.args.get("price_per_unit", ""),
        "expected_date": request.args.get("expected_date", ""),
        "source_wishlist_id": request.args.get("source_wishlist_id", ""),
        "source_restock_id": request.args.get("source_restock_id", ""),
    }
    return render_template("incoming_add.html", **_incoming_form_context(db, business_id, business, None, prefill))


@app.route("/incoming/<incoming_id>/quick-add", methods=["POST"])
def quick_add_incoming(incoming_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    incoming_item = db.execute(
        "SELECT * FROM incoming_stock WHERE id = ? AND business_id = ?", (incoming_id, business_id)
    ).fetchone()
    if not incoming_item:
        return redirect(url_for("index"))

    business = db.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    performed_by_name = _current_user_name(db, user_id)
    default_location = business["default_location"] if business else None

    session["just_added"] = apply_stock_addition(
        db, business_id, user_id, performed_by_name, default_location,
        item_name=incoming_item["item_name"], item_type=incoming_item["item_type"],
        item_color=incoming_item["item_color"], item_brand=incoming_item["item_brand"],
        item_size=incoming_item["item_size"], item_code=incoming_item["item_code"],
        location=incoming_item["location"], photo_url=incoming_item["photo_url"],
        quantity=incoming_item["quantity"], price_per_unit=incoming_item["price_per_unit"],
        vendor_id=incoming_item["vendor_id"],
    )
    db.execute("DELETE FROM incoming_stock WHERE id = ?", (incoming_id,))
    record_inventory_value_snapshot(db, business_id)
    db.commit()

    return redirect(url_for("items_tab"))


@app.route("/incoming/<incoming_id>/edit", methods=["GET", "POST"])
def edit_incoming(incoming_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    incoming_item = db.execute(
        "SELECT * FROM incoming_stock WHERE id = ? AND business_id = ?", (incoming_id, business_id)
    ).fetchone()
    if not incoming_item:
        return redirect(url_for("index"))

    if request.method == "POST":
        quantity_raw = request.form.get("quantity", "").strip()
        price_raw = request.form.get("price_per_unit", "").strip()
        expected_date = request.form.get("expected_date", "").strip()
        location = request.form.get("location", "").strip()

        error = None
        try:
            quantity = int(quantity_raw)
            price_per_unit = float(price_raw)
        except ValueError:
            error = t("err_incoming_numbers")

        if error is None and (quantity < 0 or price_per_unit < 0):
            error = t("err_incoming_negative")

        if error:
            return render_template("edit_incoming.html", incoming=incoming_item, error=error)

        db.execute(
            "UPDATE incoming_stock SET quantity = ?, price_per_unit = ?, expected_date = ?, location = ? WHERE id = ?",
            (quantity, price_per_unit, expected_date or None, location or None, incoming_id),
        )
        db.commit()

        session["just_added"] = t("msg_updated_upcoming", name=incoming_item["item_name"])
        return redirect(url_for("items_tab"))

    return render_template("edit_incoming.html", incoming=incoming_item, error=None)


@app.route("/items/<item_id>/history")
def item_history(item_id):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id = ? AND business_id = ?", (item_id, business_id)).fetchone()
    if not item:
        return redirect(url_for("index"))

    vendor = None
    if item["vendor_id"]:
        vendor = db.execute("SELECT * FROM vendors WHERE id = ? AND business_id = ?", (item["vendor_id"], business_id)).fetchone()

    sales = db.execute(
        "SELECT * FROM sales WHERE item_id = ? AND business_id = ? ORDER BY sold_at DESC",
        (item_id, business_id),
    ).fetchall()

    history = db.execute(
        "SELECT * FROM item_history WHERE item_id = ? ORDER BY occurred_at ASC",
        (item_id,),
    ).fetchall()

    chart_svg = build_quantity_chart(history)

    return render_template(
        "item_history.html",
        item=item,
        vendor=vendor,
        sales=sales,
        history=list(reversed(history)),
        chart_svg=chart_svg,
        avail=available_to_sell(item),
        low_stock_threshold=LOW_STOCK_THRESHOLD,
    )


PICKABLE_ACTIONS = {
    "sell": {"title_key": "pick_sell_title", "route": "sell_item", "only_available": True},
    "reserve": {"title_key": "pick_reserve_title", "route": "reserve_item", "only_available": True},
    "remove": {"title_key": "pick_remove_title", "route": "remove_item", "only_available": False},
}


@app.route("/pick-item/<action>")
def pick_item(action):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))
    if action not in PICKABLE_ACTIONS:
        return redirect(url_for("index"))

    db = get_db()
    config = dict(PICKABLE_ACTIONS[action])
    config["title"] = t(config["title_key"])
    items = list(
        db.execute(
            "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY item_name ASC", (business_id,)
        ).fetchall()
    )
    if config["only_available"]:
        items = [item for item in items if available_to_sell(item) > 0]

    return render_template("pick_item.html", items=items, config=config, action=action)


@app.route("/items/<item_id>/duplicate", methods=["GET", "POST"])
def duplicate_item(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    return render_template(
        "duplicate_item.html", item=item,
        item_names=distinct_item_values(db, business_id, "item_name"),
        item_types=distinct_item_values(db, business_id, "item_type"),
        item_colors=distinct_item_values(db, business_id, "item_color"),
        item_brands=distinct_item_values(db, business_id, "item_brand"),
        item_sizes=distinct_item_values(db, business_id, "item_size"),
        locations=distinct_item_values(db, business_id, "location"),
        vendors=db.execute("SELECT * FROM vendors WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall(),
    )


@app.route("/items/<item_id>/edit", methods=["GET", "POST"])
def edit_item(item_id):
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = _get_owned_item(db, item_id, business_id)
    if not item:
        return redirect(url_for("index"))

    if request.method == "POST":
        item_name = _clamp_text(request.form.get("item_name"), 150)
        item_brand = _clamp_text(request.form.get("item_brand"), 150)
        item_type = _clamp_text(request.form.get("item_type"), 150)
        item_color = _clamp_text(request.form.get("item_color"), 150)
        item_size = _clamp_text(request.form.get("item_size"), 150)
        item_code = _clamp_text(request.form.get("item_code"), 150)
        location = _clamp_text(request.form.get("location"), 150)
        price_raw = request.form.get("price_per_unit", "").strip()
        notes = _clamp_text(request.form.get("notes"), 1000)
        photo_file = request.files.get("photo")

        error = None
        if not item_name:
            error = t("err_item_name_required")
        price_per_unit = item["price_per_unit"]
        if error is None and price_raw:
            try:
                price_per_unit = float(price_raw)
            except ValueError:
                error = t("err_price_number")

        if error is None and price_per_unit < 0:
            error = t("err_price_negative")
        elif error is None and price_per_unit > 500000000:
            error = t("err_price_too_large")

        if error:
            return render_template("edit_item.html", item=item, error=error)

        photo_url = item["photo_url"]
        photo_error = None
        if photo_file and photo_file.filename:
            photo_url, photo_error = upload_photo_to_cloudinary(photo_file)

        performed_by_name = _current_user_name(db, user_id)
        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            UPDATE items SET item_name = ?, item_brand = ?, item_type = ?, item_color = ?, item_size = ?,
                              item_code = ?, location = ?, price_per_unit = ?, notes = ?, photo_url = ?,
                              last_added_by_user_id = ?, last_added_by_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                item_name, item_brand or None, item_type or None, item_color or None, item_size or None,
                item_code or None, location or None, price_per_unit, notes or None, photo_url,
                user_id, performed_by_name, now, item_id,
            ),
        )
        record_inventory_value_snapshot(db, business_id)
        db.commit()

        session["just_added"] = t("msg_updated_item", name=item_name)
        if photo_error:
            session["error"] = photo_error
        return redirect(url_for("sell_tab"))

    return render_template("edit_item.html", item=item, error=None)


@app.route("/restock/add", methods=["GET", "POST"])
def add_restock():
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    performed_by_name = _current_user_name(db, user_id)
    items = list(
        db.execute(
            "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY item_type ASC, item_name ASC",
            (business_id,),
        ).fetchall()
    )
    customers = db.execute("SELECT * FROM customers WHERE business_id = ? ORDER BY name ASC", (business_id,)).fetchall()

    if request.method == "POST":
        item_id = request.form.get("item_id", "").strip()
        customer_id_raw = request.form.get("customer_id", "").strip()
        new_customer_name = request.form.get("new_customer_name", "").strip()
        new_customer_whatsapp = request.form.get("new_customer_whatsapp", "").strip()
        date_requested_raw = request.form.get("date_requested", "").strip()

        resolved_customer_id, customer_name, resolve_error = _resolve_customer(
            db, business_id, user_id, performed_by_name, customer_id_raw, new_customer_name, new_customer_whatsapp
        )
        customer_name = customer_name or ""

        item = _get_owned_item(db, item_id, business_id)
        if resolve_error:
            return render_template(
                "restock_add.html", items=items, customers=customers, error=resolve_error,
                prefill_item_id=item_id,
            )
        if not item:
            return render_template(
                "restock_add.html", items=items, customers=customers, error=t("err_restock_required"),
                prefill_item_id=item_id,
            )

        now = datetime.now().isoformat(timespec="seconds")
        date_requested = now
        if date_requested_raw:
            try:
                date_requested = datetime.combine(date.fromisoformat(date_requested_raw), datetime.now().time()).isoformat(timespec="seconds")
            except ValueError:
                date_requested = now

        db.execute(
            """
            INSERT INTO restocks (id, business_id, item_id, item_name, customer_name, customer_key, customer_id,
                                   date_requested, requested_by_user_id, requested_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, business_id, item_id, item["item_name"], customer_name, customer_name.strip().lower(),
                resolved_customer_id, date_requested, user_id, performed_by_name, now,
            ),
        )
        db.commit()

        session["just_added"] = t("msg_restock_noted", name=item["item_name"], customer=customer_name or t("walk_in_customer"))
        return redirect(url_for("items_tab"))

    return render_template(
        "restock_add.html", items=items, customers=customers, error=None,
        prefill_item_id=request.args.get("item_id", ""),
    )


@app.route("/restock/<restock_id>/order")
def order_restock(restock_id):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    restock = db.execute("SELECT * FROM restocks WHERE id = ? AND business_id = ?", (restock_id, business_id)).fetchone()
    if not restock:
        return redirect(url_for("index"))

    item = db.execute("SELECT * FROM items WHERE id = ?", (restock["item_id"],)).fetchone()
    params = {"item_name": restock["item_name"], "source_restock_id": restock_id}
    if item:
        params.update({
            "item_type": item["item_type"] or "", "item_color": item["item_color"] or "",
            "item_brand": item["item_brand"] or "", "item_size": item["item_size"] or "",
            "item_code": item["item_code"] or "", "location": item["location"] or "",
            "price_per_unit": item["price_per_unit"],
        })
    return redirect(url_for("add_incoming", **params))


@app.route("/restock/<restock_id>/delete", methods=["POST"])
def delete_restock(restock_id):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    restock = db.execute("SELECT * FROM restocks WHERE id = ? AND business_id = ?", (restock_id, business_id)).fetchone()
    if restock:
        db.execute("DELETE FROM restocks WHERE id = ?", (restock_id,))
        db.commit()
        session["just_added"] = t("msg_restock_removed", name=restock["item_name"])

    return redirect(url_for("items_tab"))


@app.route("/wishlist/add", methods=["GET", "POST"])
def add_wishlist():
    business_id = session.get("business_id")
    user_id = session.get("user_id")
    if not business_id or not user_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    performed_by_name = _current_user_name(db, user_id)

    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()
        link_url = request.form.get("link_url", "").strip()
        notes = request.form.get("notes", "").strip()
        photo_file = request.files.get("photo")

        if not item_name:
            return render_template("wishlist_add.html", error=t("err_wishlist_name_required"), prefill=request.form)

        photo_url = None
        photo_error = None
        if photo_file and photo_file.filename:
            photo_url, photo_error = upload_photo_to_cloudinary(photo_file)

        now = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            INSERT INTO wishlist (id, business_id, item_name, link_url, photo_url, notes, added_by_user_id, added_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, business_id, item_name, link_url or None, photo_url, notes or None, user_id, performed_by_name, now),
        )
        db.commit()

        session["just_added"] = t("msg_wishlist_added", name=item_name)
        if photo_error:
            session["error"] = photo_error
        return redirect(url_for("items_tab"))

    prefill = {
        "item_name": request.args.get("item_name", ""),
        "link_url": request.args.get("link_url", ""),
        "notes": request.args.get("notes", ""),
    }
    return render_template("wishlist_add.html", error=None, prefill=prefill)


@app.route("/wishlist/<wishlist_id>/delete", methods=["POST"])
def delete_wishlist(wishlist_id):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = db.execute("SELECT * FROM wishlist WHERE id = ? AND business_id = ?", (wishlist_id, business_id)).fetchone()
    if item:
        db.execute("DELETE FROM wishlist WHERE id = ?", (wishlist_id,))
        db.commit()
        session["just_added"] = t("msg_wishlist_removed", name=item["item_name"])

    return redirect(url_for("items_tab"))


@app.route("/wishlist/<wishlist_id>/order")
def order_wishlist(wishlist_id):
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    item = db.execute("SELECT * FROM wishlist WHERE id = ? AND business_id = ?", (wishlist_id, business_id)).fetchone()
    if not item:
        return redirect(url_for("index"))

    return redirect(url_for("add_incoming", item_name=item["item_name"], source_wishlist_id=wishlist_id))


@app.route("/export.csv")
def export_csv():
    business_id = session.get("business_id")
    if not business_id:
        return redirect(url_for("enter_name"))

    db = get_db()
    items = db.execute(
        "SELECT * FROM items WHERE business_id = ? AND is_deleted = 0 ORDER BY item_name ASC", (business_id,)
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Item name", "Brand", "Type", "Color", "Size", "Code", "Location", "Quantity",
        "Reserved", "Available", "Price per unit", "Line total", "Notes", "Last added by", "Last updated",
    ])
    for item in items:
        writer.writerow([
            item["item_name"], item["item_brand"] or "", item["item_type"] or "", item["item_color"] or "",
            item["item_size"] or "", item["item_code"] or "", item["location"] or "", item["quantity"],
            item["reserved_quantity"], available_to_sell(item), item["price_per_unit"],
            item["quantity"] * item["price_per_unit"], item["notes"] or "", item["last_added_by_name"] or "",
            item["updated_at"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=enzi-inventory.csv"},
    )


# --- Buyer storefront -------------------------------------------------------
# Public pages, no login required. A buyer browses items (across all sellers,
# or scoped to one seller's shareable link), then either asks a question or
# reserves an item — both end with a pre-filled WhatsApp deep link the buyer
# sends themselves, mirroring Mezani's reservation-to-WhatsApp pattern.

ASK_SELLER_MESSAGE = (
    "Habari! Nina swali kuhusu bidhaa hii kwenye Enzi:\n"
    "Bidhaa: {item}\n"
    "Bei: {price}\n\n"
    "{question}"
)

BUY_NOW_MESSAGE = (
    "Habari! Nataka kununua bidhaa ifuatayo kwenye Enzi:\n"
    "Bidhaa: {item}\n"
    "Bei: {price}\n\n"
    "Anwani ya kupokelea: {address}\n"
    "Upatikanaji: {availability}\n"
    "Njia ya utoaji: {delivery_method}\n\n"
    "Asante!"
)


def _storefront_items(business_id=None):
    query = (
        "SELECT i.*, b.business_name AS seller_name, b.id AS seller_id, b.whatsapp_number AS seller_whatsapp "
        "FROM items i JOIN businesses b ON b.id = i.business_id "
        "WHERE i.is_deleted = 0 AND (i.quantity - i.reserved_quantity) > 0"
    )
    params = ()
    if business_id:
        query += " AND i.business_id = ?"
        params = (business_id,)
    query += " ORDER BY i.created_at DESC"
    return get_db().execute(query, params).fetchall()


def _get_storefront_item(db, item_id):
    return db.execute(
        "SELECT i.*, b.business_name AS seller_name, b.id AS seller_id, b.whatsapp_number AS seller_whatsapp "
        "FROM items i JOIN businesses b ON b.id = i.business_id WHERE i.id = ? AND i.is_deleted = 0",
        (item_id,),
    ).fetchone()


@app.route("/store")
def store_marketplace():
    return render_template("store_marketplace.html", items=_storefront_items(), seller=None)


@app.route("/store/<business_id>")
def store_seller(business_id):
    seller = get_db().execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not seller:
        return redirect(url_for("store_marketplace"))
    return render_template(
        "store_marketplace.html", items=_storefront_items(business_id), seller=seller, theme=get_theme(seller),
    )


@app.route("/store/item/<item_id>")
def store_item_detail(item_id):
    db = get_db()
    item = _get_storefront_item(db, item_id)
    if not item:
        return redirect(url_for("store_marketplace"))
    return render_template("store_item_detail.html", item=item, available=available_to_sell(item))


@app.route("/store/item/<item_id>/ask", methods=["GET", "POST"])
def store_ask_seller(item_id):
    db = get_db()
    item = _get_storefront_item(db, item_id)
    if not item:
        return redirect(url_for("store_marketplace"))

    if request.method == "POST":
        buyer_question = _clamp_text(request.form.get("buyer_question"), 500)
        message = ASK_SELLER_MESSAGE.format(
            item=item_descriptor(item), price=format_price(item["price_per_unit"]), question=buyer_question,
        )
        wa_link = build_whatsapp_link(item["seller_whatsapp"], message)
        return render_template("store_ask_seller.html", item=item, wa_link=wa_link, submitted=True)

    return render_template("store_ask_seller.html", item=item, wa_link=None, submitted=False)


@app.route("/store/item/<item_id>/buy", methods=["GET", "POST"])
def store_buy_now(item_id):
    db = get_db()
    item = _get_storefront_item(db, item_id)
    if not item:
        return redirect(url_for("store_marketplace"))
    available = available_to_sell(item)

    if request.method == "POST":
        buyer_name = _clamp_text(request.form.get("buyer_name"), 150)
        delivery_address = _clamp_text(request.form.get("delivery_address"), 500)
        buyer_availability = _clamp_text(request.form.get("buyer_availability"), 300)
        delivery_method = _clamp_text(request.form.get("delivery_method"), 150)
        quantity_raw = request.form.get("quantity", "1").strip()

        error = None
        quantity = None
        try:
            quantity = int(quantity_raw)
        except ValueError:
            error = t("err_reserve_numbers")

        if error is None and (not buyer_name or not delivery_address or not buyer_availability or not delivery_method):
            error = t("err_buy_now_fields_required")
        elif error is None and quantity <= 0:
            error = t("err_reserve_qty_min")
        elif error is None and quantity > available:
            error = t("err_reserve_qty_available", avail=available, qty=quantity)

        if error:
            return render_template("store_buy_now.html", item=item, available=available, error=error, wa_link=None)

        now = datetime.now().isoformat(timespec="seconds")
        cursor = db.execute(
            "UPDATE items SET reserved_quantity = reserved_quantity + ?, updated_at = ? "
            "WHERE id = ? AND is_deleted = 0 AND (quantity - reserved_quantity) >= ?",
            (quantity, now, item_id, quantity),
        )
        if cursor.rowcount == 0:
            db.rollback()
            fresh_item = _get_storefront_item(db, item_id)
            if not fresh_item:
                return redirect(url_for("store_marketplace"))
            return render_template(
                "store_buy_now.html", item=fresh_item, available=available_to_sell(fresh_item),
                error=t("err_stock_changed_retry"), wa_link=None,
            )

        customer_key = buyer_name.strip().lower()
        existing_customer = db.execute(
            "SELECT id FROM customers WHERE business_id = ? AND customer_key = ?",
            (item["business_id"], customer_key),
        ).fetchone()
        if existing_customer:
            customer_id = existing_customer["id"]
        else:
            customer_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO customers (id, business_id, name, customer_key, delivery_details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (customer_id, item["business_id"], buyer_name, customer_key, delivery_address, now),
            )

        agreed_price = item["price_per_unit"] * quantity
        db.execute(
            """
            INSERT INTO reservations (id, business_id, item_id, item_name, quantity, agreed_price, customer_name,
                                       customer_key, customer_id, source, buyer_delivery_address,
                                       buyer_availability, buyer_delivery_method, reserved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'buyer', ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, item["business_id"], item_id, item["item_name"], quantity, agreed_price,
                buyer_name, customer_key, customer_id, delivery_address, buyer_availability, delivery_method, now,
            ),
        )
        record_item_history(
            db, item_id, item["business_id"], "reserved", quantity, item["quantity"], now,
            None, f"{buyer_name} (Enzi storefront)",
        )
        db.commit()

        message = BUY_NOW_MESSAGE.format(
            item=item_descriptor(item), price=format_price(item["price_per_unit"]),
            address=delivery_address, availability=buyer_availability, delivery_method=delivery_method,
        )
        wa_link = build_whatsapp_link(item["seller_whatsapp"], message)
        fresh_item = _get_storefront_item(db, item_id) or item
        return render_template(
            "store_buy_now.html", item=fresh_item, available=available_to_sell(fresh_item),
            error=None, wa_link=wa_link, reserved=True,
        )

    return render_template("store_buy_now.html", item=item, available=available, error=None, wa_link=None)


# One-time demo utility: seeds a handful of fake stores/items so the buyer
# marketplace has something to look at without onboarding sellers by hand.
# Guarded by a fixed key in the URL rather than left wide open, but not
# meant to be a real permission system — the data it writes is harmless and
# idempotent (re-visiting just reports "already exists" for each store).
SEED_DEMO_KEY = "enzi-seed-2026"


@app.route("/admin/seed-demo-stores")
def seed_demo_stores_route():
    if request.args.get("key") != SEED_DEMO_KEY:
        return "Not found", 404

    import seed_demo_stores

    db = get_db()
    results = seed_demo_stores.seed_into(db)
    lines = "\n".join(results)
    return Response(
        f"{lines}\n\nDone. Log in to any store with business name shown above, "
        f"owner \"Test Test\", business PIN 1111, personal PIN 1111.\n",
        mimetype="text/plain",
    )


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
