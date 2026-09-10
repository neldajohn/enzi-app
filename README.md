# Enzi

**Simple inventory, bookkeeping, and a buyer storefront for African SME sellers.**

Enzi (formerly Dukani) is a lightweight web app built for small business owners who sell through Instagram and WhatsApp, starting with sellers in Tanzania. It replaces scattered notes, memory, and DMs with a single shared place to track stock, sales, reservations, and customers — and gives each seller a public storefront buyers can browse without an account.

## Why this exists

Most tools built for "SME bookkeeping" assume a level of formality small informal sellers don't have: no POS terminal, no accounting background, no dedicated staff. Sellers like the ones Enzi was built with track everything by memory or notebook, watch which items move fast or slow by instinct, and manage customer relationships one-on-one over WhatsApp.

Enzi started from direct interviews with active sellers (handbags, fashion & accessories, perfumes) to understand how they actually track inventory and sales today, then built around that reality rather than a generic accounting template.

## What it does

- **Inventory tracking** — add items with name, type, quantity, price, color, brand, and location; photos upload via Cloudinary
- **Sell** — record a sale for one item at a time, with a customer name, sale price independent of listed price, and a backdatable timestamp
- **Reserve** — hold stock for a customer without removing it from inventory; "Available to Sell" updates automatically
- **Remove** — track damaged, lost, expired, or personal-use stock with a reason, as an auditable record
- **Dashboard** — inventory value, items sold, low-stock alerts, with filterable time ranges
- **Multi-user businesses** — anyone registering under the same business name shares one inventory, with every action attributed to who did it
- **Buyer storefront** — a public, no-login marketplace (`/store`) of every seller's available items, plus each seller's own shareable link (`/store/<business_id>`). A buyer can ask a question or reserve an item ("Nunua Sasa" / "Uliza Muuzaji"), which opens a pre-filled WhatsApp message to the seller — Enzi never sends anything itself, it just builds the link
- **Feedback** — in-app feedback goes straight to the team via email

## Tech stack

- **Backend:** Python (Flask)
- **Database:** PostgreSQL (migrated from SQLite for deployment)
- **Photo hosting:** Cloudinary
- **Email:** Resend
- **Hosting:** Render

## Local setup

1. Clone the repo
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with:
   ```
   DATABASE_URL=your_postgres_connection_string
   CLOUDINARY_CLOUD_NAME=your_cloud_name
   CLOUDINARY_UPLOAD_PRESET=your_upload_preset
   RESEND_API_KEY=your_resend_key
   FEEDBACK_TO_EMAIL=your_feedback_inbox
   ```
4. Run the app:
   ```
   python app.py
   ```

## Deployment

Enzi is deployed on Render's free tier. Note: free-tier services spin down after 15 minutes of inactivity, so the first load after idle time may take 30–60 seconds.

## Status

Currently in active user testing with a small group of real sellers. Feature-complete for MVP scope; current focus is stress-testing and bug fixes ahead of wider rollout.

## Roadmap (not yet built)

- Waitlist / restock notifications for out-of-stock items
- Returns handling
- Swahili-language support
- Barcode scanning / AI photo item-recognition

---

Built by [your name] — a tool made with, not just for, the sellers who use it.
