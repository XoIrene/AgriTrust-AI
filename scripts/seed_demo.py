"""Load demo data: staff accounts, farmers with farms, 12 months of transactions.

Idempotent - accounts that already exist (matched by phone) are left alone.

    ./scripts/seed_demo.sh
    # or: cd backend && PYTHONPATH=. ../.venv/bin/python ../scripts/seed_demo.py
"""
from __future__ import annotations

import random
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.integrations.external_data import seed_market_prices  # noqa: E402
from app.models import (  # noqa: E402
    AccountType,
    Buyer,
    BuyerOffer,
    Crop,
    Farm,
    FarmActivity,
    FarmActivityType,
    Farmer,
    FinancialAccount,
    Language,
    MarketPrice,
    Transaction,
    TransactionType,
    User,
    UserRole,
    utcnow,
)
from app.security.crypto import encrypt_value, hash_password  # noqa: E402
from app.integrations.lenders import registry as lender_registry  # noqa: E402
from app.services import insurance as insurance_service  # noqa: E402
from app.services import loans as loan_service  # noqa: E402
from app.utils.helpers import mask_account_number  # noqa: E402

DEMO_PASSWORD = "Demo1234"


def _demo_wallet_number(phone: str) -> str:
    """A plausible Tanzanian mobile-money number for a demo farmer.

    Farmers usually settle mobile money on the same number they sign up with, so
    the login phone is reused - but only once it is a real wallet number. The
    loan payout validates the number and compares its last four digits against
    the stored mask, so both have to describe the same 12-digit number: a mask
    built from something else locks the farmer out of applying for a loan.
    """
    digits = "".join(ch for ch in phone if ch.isdigit())
    # a Tanzanian number in international form is +255 followed by 9 digits
    candidate = "+255" + digits[-9:].rjust(9, "0")
    if re.fullmatch(r"\+255[67]\d{8}", candidate):
        return candidate
    # not a Tanzanian number to begin with: build a valid one deterministically
    return "+2557" + digits[-8:].rjust(8, "0")

STAFF = [
    ("Asha Admin", "+255700000001", UserRole.ADMIN),
    ("Baraka Loan Officer", "+255700000002", UserRole.LOAN_OFFICER),
    ("Neema Insurance Officer", "+255700000003", UserRole.INSURANCE_OFFICER),
    ("Juma Cooperative Officer", "+255700000004", UserRole.COOPERATIVE_OFFICER),
]

# name, phone, language, village, district, region, crop, acres, monthly income, discipline (0..1),
# planting day relative to today (negative = already in the ground)
FARMERS = [
    ("Mary Mwakyusa", "+255754000101", Language.SW, "Mkindo", "Morogoro", "Morogoro", "Maize", 4.5, 420_000, 0.9, -50),
    ("John Kimaro", "+255754000102", Language.EN, "Ifakara", "Kilombero", "Morogoro", "Rice", 6.0, 610_000, 0.75, -40),
    # about to plant with little cash: the pre-season input gap the forecast is built to catch
    ("Fatuma Said", "+255754000103", Language.SW, "Chamwino", "Dodoma", "Dodoma", "Sorghum", 3.0, 230_000, 0.55, 20),
    ("Peter Laizer", "+255754000104", Language.EN, "Usa River", "Arusha", "Arusha", "Beans", 2.5, 310_000, 0.35, -35),
]


# name, type, region, collection point, [(crop, TZS/kg, min kg, max kg)]
BUYERS = [
    ("Morogoro Farmers Cooperative Union", "COOPERATIVE", "Morogoro", "Mkindo warehouse",
     [("Maize", 900, 500, 20000), ("Beans", 1950, 200, 5000)]),
    ("Kilombero Rice Millers", "PROCESSOR", "Morogoro", "Ifakara mill gate",
     [("Rice", 1560, 1000, 50000)]),
    ("Dodoma Grain Traders", "TRADER", "Dodoma", "Dodoma central depot",
     [("Maize", 960, 2000, None), ("Sorghum", 1060, 500, 15000)]),
    ("Arusha Bean Exporters", "EXPORTER", "Arusha", "Usa River collection centre",
     [("Beans", 2080, 300, 10000)]),
    ("Mwanza Rice Traders", "TRADER", "Mwanza", "Mwanza market yard",
     [("Rice", 1500, 500, 20000)]),
    ("Central Corridor Food Processors", "PROCESSOR", "Dodoma", "Kongwa plant",
     [("Maize", 880, 300, 30000), ("Sorghum", 1010, 300, None)]),
]


def seed_buyers(db) -> None:
    if db.execute(select(BuyerOffer.id).limit(1)).first():
        return
    for n, (name, kind, region, point, offers) in enumerate(BUYERS, start=1):
        # +255 000 ... is not an allocated mobile range, so a demo number never rings a real person
        buyer = Buyer(name=name, buyer_type=kind, region=region, contact_phone=f"+255000{n:06d}", is_verified=True)
        db.add(buyer)
        db.flush()
        for crop, price, min_kg, max_kg in offers:
            db.add(BuyerOffer(
                buyer_id=buyer.id, crop_name=crop, price_per_kg=Decimal(price),
                min_quantity_kg=Decimal(min_kg), max_quantity_kg=Decimal(max_kg) if max_kg else None,
                region=region, collection_point=point,
                valid_until=date.today() + timedelta(days=150), is_demo=True,
            ))
    db.commit()
    print(f"Buyers: {len(BUYERS)} with {sum(len(b[4]) for b in BUYERS)} offers")


def get_or_create_user(db, full_name: str, phone: str, role: UserRole, lang: Language = Language.EN) -> tuple[User, bool]:
    user = db.execute(select(User).where(User.phone == phone)).scalars().first()
    if user:
        return user, False
    user = User(
        full_name=full_name,
        phone=phone,
        email=f"{phone.lstrip('+')}@demo.agritrust.local",
        password_hash=hash_password(DEMO_PASSWORD),
        role=role,
        preferred_language=lang,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    return user, True


def seed_transactions(db, farmer: Farmer, account: FinancialAccount, monthly_income: int, rng: random.Random) -> None:
    today = utcnow()
    for month in range(12, 0, -1):
        base = today - timedelta(days=30 * month)
        # harvest months bring the big crop-sale income
        harvest = month in (2, 3, 8)
        entries = [
            (TransactionType.CROP_SALE if harvest else TransactionType.DEPOSIT,
             monthly_income * (2.2 if harvest else 0.6)),
            (TransactionType.FARM_INPUT, monthly_income * rng.uniform(0.25, 0.45)),
            (TransactionType.WITHDRAWAL, monthly_income * rng.uniform(0.2, 0.35)),
        ]
        for tx_type, amount in entries:
            direction = "in" if tx_type in (TransactionType.CROP_SALE, TransactionType.DEPOSIT) else "out"
            db.add(
                Transaction(
                    farmer_id=farmer.id,
                    account_id=account.id,
                    provider=account.provider,
                    transaction_type=tx_type,
                    amount=Decimal(str(round(amount, -2))),
                    direction=direction,
                    reference=loan_service.generate_reference("TX"),
                    transaction_date=base + timedelta(days=rng.randint(0, 27)),
                    description="Demo transaction",
                    is_sandbox=True,
                )
            )


def seed_farmer(db, spec, officer: User, rng: random.Random) -> None:
    name, phone, lang, village, district, region, crop_name, acres, income, discipline, plant_in = spec
    user, created = get_or_create_user(db, name, phone, UserRole.FARMER, lang)
    if not created:
        return
    farmer = Farmer(
        user_id=user.id,
        village=village,
        district=district,
        region=region,
        gender="female" if name.split()[0] in ("Mary", "Fatuma") else "male",
        years_farming=rng.randint(4, 22),
        savings_balance=Decimal(str(income * discipline)),
        cooperative_name=f"{district} Farmers Cooperative",
        onboarding_complete=True,
        created_via="web",
    )
    db.add(farmer)
    db.flush()

    farm = Farm(
        farmer_id=farmer.id,
        name=village,  # a place name reads the same in English and Kiswahili
        village=village,
        district=district,
        region=region,
        area_acres=Decimal(str(acres)),
        soil_type=rng.choice(["Loam", "Clay loam", "Sandy loam"]),
        irrigation_available=discipline > 0.6,
        distance_to_market_km=Decimal(str(rng.randint(3, 25))),
    )
    db.add(farm)
    db.flush()

    planting = date.today() + timedelta(days=plant_in)
    per_acre = {"Maize": 1400, "Rice": 1900, "Sorghum": 900, "Beans": 700}.get(crop_name, 1000)
    crop = Crop(
        farm_id=farm.id,
        crop_name=crop_name,
        crop_category="Cereal" if crop_name != "Beans" else "Legume",
        season="Masika",
        planting_date=planting,
        expected_harvest_date=planting + timedelta(days=120),
        expected_yield_kg=Decimal(str(per_acre * acres)),
        previous_yield_kg=Decimal(str(round(per_acre * acres * (0.6 + discipline * 0.4)))),
        seed_cost=Decimal(str(35_000 * acres)),
        fertilizer_cost=Decimal(str(60_000 * acres)),
        labour_cost=Decimal(str(45_000 * acres)),
        equipment_cost=Decimal("40000"),
        is_current=True,
    )
    db.add(crop)
    db.flush()

    for offset, activity in ((0, FarmActivityType.LAND_PREPARATION), (7, FarmActivityType.PLANTING),
                             (28, FarmActivityType.FERTILIZING), (40, FarmActivityType.WEEDING)):
        day = planting - timedelta(days=7) + timedelta(days=offset)
        if day <= date.today():
            db.add(FarmActivity(farm_id=farm.id, crop_id=crop.id, activity_type=activity,
                                activity_date=day, cost=Decimal("25000"), labour_count=rng.randint(1, 5)))

    # The payout account must be internally consistent: the masked value and the
    # encrypted value have to describe the SAME number. Seeding only the mask -
    # built from the login phone - made every loan application fail, because the
    # farmer is asked to retype a full number and then told it "does not match the
    # saved account" no matter what they type. A real wallet number is stored
    # encrypted instead, so the "enter the full number once" step never appears.
    wallet = _demo_wallet_number(phone)
    account = FinancialAccount(
        farmer_id=farmer.id,
        provider=rng.choice(["mpesa", "airtel_money", "mixx_by_yas", "halopesa"]),
        account_type=AccountType.MOBILE_MONEY,
        account_number_masked=mask_account_number(wallet),
        account_number_encrypted=encrypt_value(wallet),
        account_name=name,
        balance=Decimal(str(round(income * discipline * 0.8, -2))),
        is_primary=True,
        is_sandbox=True,
    )
    db.add(account)
    db.flush()
    seed_transactions(db, farmer, account, income, rng)
    db.commit()

    # an older, fully repaid loan builds repayment history for the risk model
    if discipline >= 0.5:
        past = loan_service.create_application(
            db, farmer, requested_amount=Decimal(str(income)), purpose="Inputs for last season's crop",
            term_months=6, farm_id=farm.id, crop_id=crop.id,
        )
        loan = loan_service.approve_application(db, past, officer, reason="Good repayment capacity")
        # last season's loan: date the application and decision to match its backdated repayments
        applied = utcnow() - timedelta(days=30 * 8)
        past.created_at = applied
        past.decided_at = applied + timedelta(days=rng.randint(2, 6))
        loan.created_at = loan.disbursed_at = past.decided_at
        loan_service.disburse(db, loan)
        for i in range(6):
            loan_service.record_repayment(
                db, loan, amount=loan.monthly_payment,
                payment_date=date.today() - timedelta(days=30 * (7 - i)),
                is_on_time=rng.random() < discipline,
            )

    # a fresh application waiting in the officer queue
    loan_service.create_application(
        db, farmer, requested_amount=Decimal(str(income * 2)),
        purpose=f"Fertiliser and seed for {acres} acres of {crop_name.lower()}",
        term_months=12, farm_id=farm.id, crop_id=crop.id,
    )

    if discipline > 0.5:
        insurance_service.create_policy(
            db, farmer, farm_id=farm.id, crop_id=crop.id,
            product_code=insurance_service.DEFAULT_PRODUCTS[0]["code"],
            start_date=planting,
        )
        # A second policy that is still ACTIVE, so the farmer can watch a trigger
        # progress instead of only seeing a finished claim. The flood product's
        # window is open from day 0 and its threshold is a 3-day rainfall peak
        # that ordinary weather does not reach, so it stays active until an
        # officer runs the trigger scan.
        insurance_service.create_policy(
            db, farmer, farm_id=farm.id, crop_id=crop.id,
            product_code=insurance_service.DEFAULT_PRODUCTS[2]["code"],
            start_date=date.today() - timedelta(days=45),
        )
    print(f"  farmer  {phone}  {name} ({district}, {crop_name})")


def main() -> None:
    Base.metadata.create_all(bind=engine)
    rng = random.Random(42)
    with SessionLocal() as db:
        insurance_service.ensure_products(db)
        # demo SACCO / bank / mobile-money lenders the farmer can be matched to
        lender_rows = lender_registry.ensure_lenders(db)
        print(f"Lenders: {len(lender_rows)} demo lenders across SACCO / bank / mobile money")
        seed_buyers(db)
        if not db.execute(select(MarketPrice.id).limit(1)).first():
            seed_market_prices(db)
        print("Staff accounts:")
        for name, phone, role in STAFF:
            _, created = get_or_create_user(db, name, phone, role)
            print(f"  {role.value:<20} {phone}  {'created' if created else 'exists'}")
        db.commit()
        officer = db.execute(select(User).where(User.role == UserRole.LOAN_OFFICER)).scalars().first()
        print("Farmers:")
        for spec in FARMERS:
            seed_farmer(db, spec, officer, rng)
    print(f"\nAll demo accounts use password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
