"""Add a demo BUYER account, listings and paid/pending orders (idempotent).

Run:  cd backend && PYTHONPATH=. ../.venv/bin/python ../scripts/seed_buyer_demo.py

The orders go through the same two tables the real flow writes - a
``CropOrder`` and the ``PaymentRequest`` raised for it - and are settled with
``collections.settle``, so the buyer's transaction statement shows genuine
records rather than rows invented for the demo. One order per payment type, so
every rail on the platform is visible on the statement.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    CropListing,
    CropOrder,
    Farmer,
    OrderStatus,
    PaymentRequest,
    User,
    UserRole,
    utcnow,
)
from app.security.crypto import hash_password  # noqa: E402
from app.services import collections  # noqa: E402
from app.routers.marketplace import _order_reference  # noqa: E402
from app.config import settings  # noqa: E402

DEMO_PASSWORD = "Demo1234"

#: (crop, kg, TZS/kg, quality, provider key) - one per kind of payment rail
DEMO_ORDERS = [
    ("Maize", "900", "850", "A", "mpesa", "PAID"),
    ("Beans", "450", "2285", "B", "airtel_money", "PAID"),
    ("Rice", "600", "1450", "A", "halopesa", "PAID"),
    ("Sorghum", "300", "980", "C", "nmb", "PAID"),
    ("Maize", "250", "870", "A", "crdb", "PENDING"),
    ("Beans", "180", "2300", "B", "selcom", "PENDING"),
]


def _listing(db, farmer, crop, kg, price, quality):
    return CropListing(
        farmer_id=farmer.id,
        crop_name=crop,
        quantity_kg=Decimal(kg),
        price_per_kg=Decimal(price),
        quality_grade=quality,
        location=farmer.district or "Morogoro",
        description=f"Demo {crop.lower()}, ready for collection.",
    )


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == "+255754000201").first()
        if user is None:
            user = User(
                email="buyer@demo.agritrust.local",
                phone="+255754000201",
                password_hash=hash_password(DEMO_PASSWORD),
                full_name="Bora Buyer",
                role=UserRole.BUYER,
            )
            db.add(user)
            db.commit()
            print("buyer account created: +255754000201 / Demo1234")
        else:
            print("buyer account exists")

        farmer = db.query(Farmer).first()
        if farmer is None:
            print("no farmer to list against; run seed_demo.py first")
            return

        if db.query(CropListing).count() == 0:
            db.add(_listing(db, farmer, "Maize", "1200", "850", "A"))
            db.commit()
            print("demo listing created")

        # already seeded?
        if db.query(CropOrder).filter(CropOrder.buyer_user_id == user.id).count() > 0:
            print("demo orders already exist")
            print("done")
            return

        made = 0
        for crop, kg, price, quality, provider, status in DEMO_ORDERS:
            listing = _listing(db, farmer, crop, kg, price, quality)
            db.add(listing)
            db.flush()

            total = (Decimal(kg) * Decimal(price)).quantize(Decimal("1"))
            order = CropOrder(
                reference=_order_reference(),
                listing_id=listing.id,
                buyer_user_id=user.id,
                farmer_id=farmer.id,
                quantity_kg=Decimal(kg),
                total_amount=total,
                status=OrderStatus.PENDING_PAYMENT,
            )
            db.add(order)
            db.flush()

            pay = PaymentRequest(
                reference=collections.new_reference(),
                farmer_id=farmer.id,
                provider=provider,
                purpose="CROP_PURCHASE",
                target_id=order.id,
                amount=total,
                payer_phone=user.phone,
                status="PENDING",
                expires_at=utcnow() + timedelta(minutes=settings.payment_request_minutes),
                is_sandbox=True,
            )
            db.add(pay)
            db.flush()
            order.payment_reference = pay.reference

            if status == "PAID":
                # settle through the real service so the listing closes too
                collections.settle(db, pay, provider_reference=f"DEMO-{pay.reference}")
                pay.provider_reference = f"DEMO-{pay.reference}"
                made += 1
            db.commit()

        print(f"demo orders created: {made} paid, {len(DEMO_ORDERS) - made} awaiting payment")
        print("done")
    finally:
        db.close()


if __name__ == "__main__":
    main()
