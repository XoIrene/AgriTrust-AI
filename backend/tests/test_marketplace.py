"""Buyer marketplace + loan-officer dashboard tests.

Covers the buyer journey end to end on the sandbox rail (listing -> order ->
payment page -> settle -> listing SOLD) and the officer dashboard aggregates.
No real payment provider is contacted: everything runs on the mock adapters.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import Farmer, User, UserRole
from app.security.crypto import hash_password


@pytest.fixture(scope="module")
def client():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///./test_marketplace.db", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
            db.rollback()
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    import os

    os.remove("./test_marketplace.db")


@pytest.fixture()
def at_db(client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///./test_marketplace.db", connect_args={"check_same_thread": False})
    S = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    session = S()
    yield session
    session.close()


def _verified_ticket(client, nida_number, phone):
    """Walk the real registration path: NIDA -> phone OTP -> ticket.

    Registration now requires a verified identity, so any test that creates an
    account the way a person does has to earn the ticket first. ``nida_number``
    must be unique per account, because one national ID backs one account.
    """
    from app.routers import identity as identity_router

    # the rate limiter is process-wide; keep these signups from starving each other
    identity_router._verify_limiter.reset()
    identity_router._otp_limiter.reset()

    verified = client.post("/api/v1/identity/nida/verify", json={"nida_number": nida_number})
    assert verified.status_code == 200, verified.text
    verification_id = verified.json()["verification_id"]

    sent = client.post(
        "/api/v1/identity/otp/request", json={"verification_id": verification_id, "phone": phone}
    )
    assert sent.status_code == 200, sent.text
    code = sent.json()["dev_code"]

    confirmed = client.post(
        "/api/v1/identity/otp/verify", json={"verification_id": verification_id, "code": code}
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["identity_ticket"]


def _test_nida_for(phone: str) -> str:
    """A reserved, auto-recognised test NIDA derived from a phone number.

    Deterministic, so re-running a test reuses the same identity rather than
    tripping the one-NIDA-per-account rule, and distinct per phone, so two
    fixtures never collide.
    """
    import hashlib

    from app.integrations.identity.mock import MOCK_TEST_RANGE_PREFIX

    tail = int(hashlib.sha256(phone.encode("utf-8")).hexdigest(), 16) % 10**12
    return f"{MOCK_TEST_RANGE_PREFIX}{tail:012d}"


def _signup(client, db, phone, role, name, nida_number=None):
    user = db.query(User).filter(User.phone == phone).first()
    if user is None:
        payload = {
            "full_name": name,
            "phone": phone,
            "password": "Passw0rd!123",
            "role": role,
        }
        if role == "FARMER":
            payload.update({"district": "Morogoro", "region": "Morogoro"})
        payload["identity_ticket"] = _verified_ticket(
            client, nida_number or _test_nida_for(phone), phone
        )
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 201, response.text
        user = db.query(User).filter(User.phone == phone).first()
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": "Passw0rd!123"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def farmer_token(client, at_db):
    return _signup(client, at_db, "+255754000301", "FARMER", "Listing Farmer")


@pytest.fixture()
def buyer_token(client, at_db):
    return _signup(client, at_db, "+255754000302", "BUYER", "Bora Buyer")


@pytest.fixture()
def loan_ready(at_db):
    """Everything /loans/apply insists on, and a clean slate to apply against.

    The module shares one database file, and a farmer is only allowed one open
    application at a time, so each test starts with the earlier ones cleared and
    the farm, crop and payout account in place.
    """
    from decimal import Decimal

    from app.models import AccountType, Crop, Farm, Farmer, FinancialAccount, LoanApplication
    from app.security.crypto import encrypt_value

    farmer = at_db.query(Farmer).filter(Farmer.user_id == _user_id(at_db, "+255754000301")).first()
    at_db.query(LoanApplication).filter(LoanApplication.farmer_id == farmer.id).delete()
    at_db.commit()

    farm = at_db.query(Farm).filter(Farm.farmer_id == farmer.id).first()
    if farm is None:
        farm = Farm(farmer_id=farmer.id, name="Home plot", district="Morogoro", region="Morogoro", area_acres=Decimal("5"))
        at_db.add(farm)
        at_db.commit()
        at_db.refresh(farm)
    crop = at_db.query(Crop).filter(Crop.farm_id == farm.id).first()
    if crop is None:
        crop = Crop(farm_id=farm.id, crop_name="Maize", is_current=True,
                    expected_yield_kg=Decimal("3000"), seed_cost=Decimal("120000"),
                    fertilizer_cost=Decimal("80000"))
        at_db.add(crop)
        at_db.commit()
    account = at_db.query(FinancialAccount).filter(FinancialAccount.farmer_id == farmer.id).first()
    if account is None:
        # the number has to be stored encrypted, otherwise the payout service
        # cannot reuse it and makes the farmer type it again
        account = FinancialAccount(
            farmer_id=farmer.id, provider="mpesa", account_type=AccountType.MOBILE_MONEY,
            account_number_masked="****0101", account_number_encrypted=encrypt_value("0675010101"),
            account_name="Listing Farmer", balance=Decimal("500000"), is_primary=True,
        )
        at_db.add(account)
        at_db.commit()
        at_db.refresh(account)
    return {"farm_id": farm.id, "account_id": account.id}


@pytest.fixture()
def officer_token(client, at_db):
    return _signup(client, at_db, "+255754000303", "LOAN_OFFICER", "Deka Officer")


def test_buyer_dashboard_requires_auth(client):
    assert client.get("/api/v1/buyer/dashboard").status_code == 401


def test_farmer_cannot_access_buyer_dashboard(client, farmer_token):
    response = client.get("/api/v1/buyer/dashboard", headers={"Authorization": f"Bearer {farmer_token}"})
    assert response.status_code == 403


def test_farmer_posts_listing_and_buyer_sees_it(client, farmer_token, buyer_token):
    response = client.post(
        "/api/v1/market/listings",
        json={"crop_name": "maize", "quantity_kg": 500, "price_per_kg": 900, "quality_grade": "A"},
        headers={"Authorization": f"Bearer {farmer_token}"},
    )
    assert response.status_code == 201, response.text
    listing = response.json()
    assert listing["crop_name"] == "Maize"  # normalised to title case
    assert listing["total_value"] == 450000

    response = client.get(
        "/api/v1/market/listings/available", headers={"Authorization": f"Bearer {buyer_token}"}
    )
    assert response.status_code == 200
    assert any(l["id"] == listing["id"] for l in response.json())


def test_buyer_orders_and_pays_through_sandbox_rail(client, farmer_token, buyer_token, at_db):
    response = client.post(
        "/api/v1/market/listings",
        json={"crop_name": "Rice", "quantity_kg": 200, "price_per_kg": 1500},
        headers={"Authorization": f"Bearer {farmer_token}"},
    )
    listing = response.json()

    response = client.post(
        "/api/v1/buyer/orders",
        json={"listing_id": listing["id"], "provider": "mpesa"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "PENDING_PAYMENT"
    assert order["total_amount"] == 300000
    assert order["pay_url"].startswith("/pay/")

    # the payment page the buyer is sent to must exist and show the amount
    reference = order["payment_reference"]
    page = client.get(f"/pay/{reference}")
    assert page.status_code == 200

    # sandbox confirm (what the wallet callback does in production)
    confirm = client.post(f"/api/v1/payments/requests/{reference}/confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "PAID"

    # the order and the listing close in the same flow
    orders = client.get("/api/v1/buyer/orders", headers={"Authorization": f"Bearer {buyer_token}"}).json()
    mine = next(o for o in orders if o["id"] == order["id"])
    assert mine["status"] == "PAID"
    listings = client.get(
        "/api/v1/market/listings/available", headers={"Authorization": f"Bearer {buyer_token}"}
    ).json()
    assert all(l["id"] != listing["id"] for l in listings)  # no longer on sale

    # the farmer sees the sale money as a booked transaction via the existing rail


def test_order_rejects_overlong_quantity(client, farmer_token, buyer_token):
    response = client.post(
        "/api/v1/market/listings",
        json={"crop_name": "Beans", "quantity_kg": 100, "price_per_kg": 2000},
        headers={"Authorization": f"Bearer {farmer_token}"},
    )
    listing = response.json()
    response = client.post(
        "/api/v1/buyer/orders",
        json={"listing_id": listing["id"], "quantity_kg": 500, "provider": "mpesa"},
        headers={"Authorization": f"Bearer {buyer_token}"},
    )
    assert response.status_code == 422


def test_officer_dashboard_requires_officer_role(client, buyer_token, officer_token):
    assert client.get(
        "/api/v1/officer/loan-dashboard", headers={"Authorization": f"Bearer {buyer_token}"}
    ).status_code == 403
    response = client.get(
        "/api/v1/officer/loan-dashboard", headers={"Authorization": f"Bearer {officer_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"queue", "portfolio", "pending", "approved"}
    assert set(body["portfolio"]) >= {"active_loans", "disbursed_total", "repaid_total", "recovery_rate"}
    # the desk also carries analytics, a collections worklist and lender exposure
    assert set(body) >= {"analytics", "collections", "exposure", "institutions"}
    assert set(body["analytics"]) >= {
        "approval_rate", "avg_ticket", "avg_decision_hours", "avg_term_months",
        "risk_distribution", "status_distribution", "non_performing_ratio",
    }
    assert set(body["portfolio"]["par"]) == {"par1", "par30", "par60", "par90"}


def _seed_application(client, at_db, farmer_token):
    """Create a real farm, crop and application so the officer has a file to work."""
    from decimal import Decimal

    from app.models import Crop, Farm
    from app.services import loans as loan_service

    farmer = at_db.query(Farmer).filter(Farmer.user_id == _user_id(at_db, "+255754000301")).first()
    farm = Farm(farmer_id=farmer.id, name="Home plot", district="Morogoro", region="Morogoro", area_acres=Decimal("5"))
    at_db.add(farm)
    at_db.commit()
    at_db.refresh(farm)
    crop = Crop(farm_id=farm.id, crop_name="Maize", is_current=True,
                expected_yield_kg=Decimal("3000"), seed_cost=Decimal("120000"), fertilizer_cost=Decimal("80000"))
    at_db.add(crop)
    at_db.commit()
    at_db.refresh(crop)
    application = loan_service.create_application(
        at_db, farmer, requested_amount=Decimal("500000"),
        purpose="Buy fertiliser and certified seed for the new season",
        farm_id=farm.id, crop_id=crop.id, recommended_max=Decimal("600000"),
    )
    return application.id


def _user_id(db, phone):
    return db.query(User).filter(User.phone == phone).first().id


def test_officer_loan_file_note_and_decision_flow(client, farmer_token, buyer_token, officer_token, at_db):
    from decimal import Decimal

    application_id = _seed_application(client, at_db, farmer_token)

    # a buyer may not read the underwriting file
    assert client.get(
        f"/api/v1/officer/applications/{application_id}",
        headers={"Authorization": f"Bearer {buyer_token}"},
    ).status_code == 403

    response = client.get(
        f"/api/v1/officer/applications/{application_id}",
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert response.status_code == 200, response.text
    file = response.json()
    assert set(file) >= {
        "application", "farmer", "requirements", "risk", "financial_profile",
        "farms", "institutions", "preview", "schedule", "history", "notes",
    }
    assert file["application"]["id"] == application_id
    assert file["preview"]["monthly_payment"] > 0
    assert file["institutions"]["tiers"]["eligible"]["lenders"]  # demo lenders resolve

    # an officer note is written to the file and read back
    note = client.post(
        f"/api/v1/officer/applications/{application_id}/note",
        json={"note": "Called the farmer; farm and inputs verified on site."},
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert note.status_code == 200, note.text
    file = client.get(
        f"/api/v1/officer/applications/{application_id}",
        headers={"Authorization": f"Bearer {officer_token}"},
    ).json()
    assert any(n["note"].startswith("Called the farmer") for n in file["notes"])

    # the officer binds the application to an institution
    lender = file["institutions"]["tiers"]["eligible"]["lenders"][0]
    assigned = client.post(
        f"/api/v1/loans/{application_id}/assign-lender",
        json={"lender_code": lender["code"]},
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["lender_code"] == lender["code"]

    # approve with explicit terms and covenants
    approved = client.post(
        f"/api/v1/loans/{application_id}/approve",
        json={
            "approved_amount": 450000, "term_months": 10, "interest_rate": 11.5,
            "lender_code": lender["code"], "conditions": "Insurance within 30 days",
            "reason": "Strong records and a clear input plan",
        },
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert approved.status_code == 200, approved.text
    detail = approved.json()
    assert detail["status"] == "APPROVED"
    assert detail["term_months"] == 10
    assert float(detail["interest_rate"]) == 11.5
    assert "Insurance within 30 days" in (detail["decision_reason"] or "")

    # once a loan exists, the officer's file carries the booked amortisation
    after = client.get(
        f"/api/v1/officer/applications/{application_id}",
        headers={"Authorization": f"Bearer {officer_token}"},
    ).json()
    assert len(after["schedule"]) == 10
    assert after["schedule"][0]["installment"] == 1

    # the collections reminder reaches the farmer on the sandbox rail
    reminder = client.post(
        f"/api/v1/officer/applications/{application_id}/remind",
        json={"channel": "BOTH"},
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert reminder.status_code == 200, reminder.text
    assert set(reminder.json()["channels"]) == {"SMS", "IN_APP"}


def test_officer_financial_institutions_lists_wallets_and_rails(client, officer_token):
    response = client.get(
        "/api/v1/officer/financial-institutions",
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert set(body["payment_providers"]) >= {"MOBILE_MONEY", "BANK"}
    for rows in body["institutions"].values():
        for lender in rows:
            assert "wallet_name" in lender and "area" in lender


def test_officer_sees_buyer_activity(client, officer_token):
    response = client.get(
        "/api/v1/officer/buyer-activity", headers={"Authorization": f"Bearer {officer_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["orders_total"] >= 1
    assert body["orders_paid_value"] >= 300000


# ===========================================================================
# Loan purpose categories
#
# A farmer picks what the loan is for - fertiliser, drilling, equipment, money -
# so applications can be grouped and an officer can see the use without reading
# every sentence. The free text stays optional once a category is chosen.
# ===========================================================================
class TestLoanPurpose:
    def _eligible_farmer(self, at_db):
        farmer = at_db.query(Farmer).filter(
            Farmer.user_id == _user_id(at_db, "+255754000301")
        ).first()
        return farmer

    def test_a_picked_category_is_enough_on_its_own(self, client, farmer_token, loan_ready):
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "1500000",
                "term_months": 12,
                "purpose_code": "FERTILIZER",
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["purpose_code"] == "FERTILIZER"
        # the category is recorded as the purpose text too, so an officer reading
        # only the words still sees what the money is for
        assert "Fertilizer" in body["purpose"]

    @pytest.mark.parametrize(
        "code, expected",
        [
            ("FERTILIZER", "Fertilizer"),
            ("DRILLING", "Drilling"),
            ("EQUIPMENT", "Equipment"),
            ("MONEY", "Farm money"),
        ],
    )
    def test_every_use_a_farmer_names_is_offered(
        self, client, farmer_token, loan_ready, code, expected
    ):
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "1200000",
                "term_months": 12,
                "purpose_code": code,
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 201, res.text
        assert expected in res.json()["purpose"]

    def test_the_farmers_own_words_are_kept_alongside_the_category(
        self, client, farmer_token, loan_ready
    ):
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "900000",
                "term_months": 12,
                "purpose_code": "DRILLING",
                "purpose": "Planting the bottom field before the short rains",
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["purpose_code"] == "DRILLING"
        assert body["purpose"] == "Planting the bottom field before the short rains"

    def test_an_application_can_still_be_made_with_words_only(
        self, client, farmer_token, loan_ready
    ):
        """The USSD path and any older client still send free text only."""
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "800000",
                "term_months": 12,
                "purpose": "Replacing a broken water pump for the nursery",
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 201, res.text
        assert res.json()["purpose_code"] is None

    def test_an_application_with_neither_is_refused(self, client, farmer_token, loan_ready):
        """Otherwise a loan could be requested with no stated use at all."""
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "800000",
                "term_months": 12,
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 422

    def test_an_unknown_category_is_refused(self, client, farmer_token, loan_ready):
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "800000",
                "term_months": 12,
                "purpose_code": "YACHT",
                "purpose": "A boat for the lake, honestly quite large",
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 422

    def test_the_swahili_category_text_is_swahili(self, client, farmer_token, loan_ready):
        res = client.post(
            "/api/v1/loans/apply",
            json={
                "requested_amount": "700000",
                "term_months": 12,
                "purpose_code": "EQUIPMENT",
                "language": "sw",
                "farm_id": loan_ready["farm_id"],
                "payout_account_id": loan_ready["account_id"],
            },
            headers={"Authorization": f"Bearer {farmer_token}", "Accept-Language": "sw"},
        )
        assert res.status_code == 201, res.text
        assert "Vifaa" in res.json()["purpose"]


# ===========================================================================
# Farm photos
#
# The farmer can photograph the farm they are insuring, because an officer reads
# the field before approving cover. Anyone with an account can reach the upload
# endpoint, so the tests below pin the rules that make that safe: only the owner
# may write to their farm, the name on disk is the server's, and the type is
# decided by the bytes rather than by what the client claims.
# ===========================================================================
class TestFarmPhoto:
    def _farm(self, at_db, phone="+255754000301"):
        farmer = at_db.query(Farmer).filter(Farmer.user_id == _user_id(at_db, phone)).first()
        from app.models import Farm
        farm = at_db.query(Farm).filter(Farm.farmer_id == farmer.id).first()
        if farm is None:
            from decimal import Decimal
            farm = Farm(farmer_id=farmer.id, name="Home plot", district="Morogoro",
                        region="Morogoro", area_acres=Decimal("5"))
            at_db.add(farm)
            at_db.commit()
            at_db.refresh(farm)
        return farm

    def test_a_farmer_uploads_and_the_url_comes_back(self, client, farmer_token, at_db):
        from app.models import Farm

        farm = self._farm(at_db)
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("field.jpg", _jpeg_bytes(), "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"] == farm.id
        assert body["photo_url"].startswith("/static/uploads/farms/")
        # the stored name is the server's, not the one the client sent
        assert "field.jpg" not in body["photo_url"]
        # the request committed in its own session, so re-read rather than trust
        # the object this fixture is still holding
        at_db.expire_all()
        stored = at_db.query(Farm).get(farm.id).photo_filename
        assert stored and stored.endswith(".jpg")

    def test_the_stored_file_really_exists_and_is_served_path(self, client, farmer_token, at_db):
        from app.services.uploads import upload_dir
        from app.models import Farm

        farm = self._farm(at_db)
        payload = _jpeg_bytes()
        client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("whatever.png", payload, "image/png")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        at_db.expire_all()
        stored = at_db.query(Farm).get(farm.id).photo_filename
        assert stored
        assert (upload_dir() / stored).read_bytes() == payload

    def test_a_renamed_text_file_is_refused(self, client, farmer_token, at_db):
        """The type is decided by the bytes, so a .jpg of plain text is not a photo."""
        farm = self._farm(at_db)
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("fake.jpg", b"<?php system($_GET[0]); ?>", "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 422
        assert "not a photo" in res.text

    def test_a_disguised_svg_is_refused(self, client, farmer_token, at_db):
        """SVG runs script and is served from the app's own origin, so it is out."""
        farm = self._farm(at_db)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("x.svg", svg, "image/svg+xml")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 422
        assert "not a photo" in res.text

    def test_an_oversized_photo_is_refused(self, client, farmer_token, at_db):
        farm = self._farm(at_db)
        big = b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024 + 10)
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("huge.jpg", big, "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 422
        assert "5 MB" in res.text

    def test_an_empty_file_is_refused(self, client, farmer_token, at_db):
        farm = self._farm(at_db)
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("empty.jpg", b"", "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 422
        assert "Choose a photo" in res.text

    def test_a_rejected_upload_keeps_the_photo_already_there(self, client, farmer_token, at_db):
        from app.models import Farm

        farm = self._farm(at_db)
        first = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("ok.jpg", _jpeg_bytes(), "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        ).json()
        # a bad second attempt must not cost them the photo they had
        client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("bad.jpg", b"not an image at all", "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        at_db.expire_all()
        assert at_db.query(Farm).get(farm.id).photo_filename == first["photo_url"].rsplit("/", 1)[-1]

    def test_replacing_removes_the_old_file(self, client, farmer_token, at_db):
        from app.services.uploads import upload_dir
        from app.models import Farm

        farm = self._farm(at_db)
        client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("one.jpg", _jpeg_bytes(), "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        at_db.expire_all()
        first = at_db.query(Farm).get(farm.id).photo_filename
        client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("two.png", _png_bytes(), "image/png")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        at_db.expire_all()
        second = at_db.query(Farm).get(farm.id).photo_filename
        assert first != second
        # the replaced file is cleaned up rather than left behind forever
        assert not (upload_dir() / first).exists()
        assert (upload_dir() / second).exists()

    def test_another_signed_in_user_cannot_upload_to_someone_elses_farm(
        self, client, farmer_token, buyer_token, at_db
    ):
        """A buyer is a real account, so this is the cross-user case that matters."""
        from app.models import Farm

        farm = self._farm(at_db)
        # this module shares one database, so compare against whatever is there
        # now rather than assuming the farm starts without a photo
        at_db.expire_all()
        before = at_db.query(Farm).get(farm.id).photo_filename
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("mine.jpg", _jpeg_bytes(), "image/jpeg")},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert res.status_code in (403, 404)
        at_db.expire_all()
        assert at_db.query(Farm).get(farm.id).photo_filename == before

    def test_uploading_needs_a_login(self, client, at_db):
        farm = self._farm(at_db)
        res = client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("x.jpg", _jpeg_bytes(), "image/jpeg")},
        )
        assert res.status_code == 401

    def test_the_farmer_can_take_the_photo_down_again(self, client, farmer_token, at_db):
        from app.models import Farm

        farm = self._farm(at_db)
        client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("gone.jpg", _jpeg_bytes(), "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        res = client.delete(
            f"/api/v1/farms/{farm.id}/photo",
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        assert res.status_code == 200
        assert res.json()["photo_url"] is None
        at_db.expire_all()
        assert at_db.query(Farm).get(farm.id).photo_filename is None

    def test_the_listing_shows_the_photo_url(self, client, farmer_token, at_db):
        farm = self._farm(at_db)
        client.post(
            f"/api/v1/farms/{farm.id}/photo",
            files={"photo": ("list.jpg", _jpeg_bytes(), "image/jpeg")},
            headers={"Authorization": f"Bearer {farmer_token}"},
        )
        farms = client.get("/api/v1/farms", headers={"Authorization": f"Bearer {farmer_token}"}).json()
        row = next(f for f in farms if f["id"] == farm.id)
        assert row["photo_url"].startswith("/static/uploads/farms/")


def _jpeg_bytes() -> bytes:
    """The smallest byte string that really is a JPEG, for the magic-byte check."""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 32 + b"\xff\xd9"


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


# ===========================================================================
# Whose record is it
#
# ensure_farmer_access used to refuse only a FARMER reaching for someone else's
# row, which quietly let every other signed-in role - a buyer, say - read, change
# and delete any farmer's farm, crop and activity. The roles allowed to work
# across farmers are now named, and a buyer is not one of them.
# ===========================================================================
class TestFarmRecordOwnership:
    def _farm(self, at_db, phone="+255754000301"):
        from decimal import Decimal

        from app.models import Farm
        farmer = at_db.query(Farmer).filter(Farmer.user_id == _user_id(at_db, phone)).first()
        farm = at_db.query(Farm).filter(Farm.farmer_id == farmer.id).first()
        if farm is None:
            farm = Farm(farmer_id=farmer.id, name="Home plot", district="Morogoro",
                        region="Morogoro", area_acres=Decimal("5"))
            at_db.add(farm)
            at_db.commit()
            at_db.refresh(farm)
        return farm

    def test_a_buyer_cannot_read_a_farm_they_do_not_own(self, client, farmer_token, buyer_token, at_db):
        farm = self._farm(at_db)
        res = client.get(f"/api/v1/farms/{farm.id}", headers={"Authorization": f"Bearer {buyer_token}"})
        assert res.status_code in (403, 404)

    def test_a_buyer_cannot_rename_a_farm_they_do_not_own(self, client, farmer_token, buyer_token, at_db):
        farm = self._farm(at_db)
        res = client.patch(
            f"/api/v1/farms/{farm.id}",
            json={"name": "Taken Over"},
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert res.status_code in (403, 404)
        at_db.expire_all()
        from app.models import Farm
        assert at_db.query(Farm).get(farm.id).name == "Home plot"

    def test_a_buyer_cannot_archive_a_farm_they_do_not_own(self, client, farmer_token, buyer_token, at_db):
        farm = self._farm(at_db)
        res = client.delete(f"/api/v1/farms/{farm.id}", headers={"Authorization": f"Bearer {buyer_token}"})
        assert res.status_code in (403, 404)

    def test_the_owner_can_still_do_all_three(self, client, farmer_token, at_db):
        farm = self._farm(at_db)
        auth = {"Authorization": f"Bearer {farmer_token}"}
        assert client.get(f"/api/v1/farms/{farm.id}", headers=auth).status_code == 200
        assert client.patch(
            f"/api/v1/farms/{farm.id}", json={"name": "Home plot"}, headers=auth
        ).status_code == 200

    def test_staff_still_work_across_farmers(self, client, farmer_token, officer_token, at_db):
        """A loan officer underwrites any farmer, so the desk must still work."""
        farm = self._farm(at_db)
        res = client.get(f"/api/v1/farms/{farm.id}", headers={"Authorization": f"Bearer {officer_token}"})
        assert res.status_code == 200
        assert res.json()["id"] == farm.id
