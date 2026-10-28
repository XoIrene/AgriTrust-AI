"""SQLAlchemy models for AgriTrust AI.

The schema is intentionally normalised and readable: users -> farmers -> farms
-> crops/activities, plus finance, credit, insurance, external data, AI
predictions, notifications and audit logs.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class UserRole(str, enum.Enum):
    FARMER = "FARMER"
    BUYER = "BUYER"
    LOAN_OFFICER = "LOAN_OFFICER"
    INSURANCE_OFFICER = "INSURANCE_OFFICER"
    COOPERATIVE_OFFICER = "COOPERATIVE_OFFICER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class Language(str, enum.Enum):
    EN = "en"
    SW = "sw"


class AccountType(str, enum.Enum):
    BANK = "BANK"
    MOBILE_MONEY = "MOBILE_MONEY"
    SAVINGS = "SAVINGS"
    OTHER = "OTHER"


class TransactionType(str, enum.Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FARM_INPUT = "FARM_INPUT"
    CROP_SALE = "CROP_SALE"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"
    LOAN_REPAYMENT = "LOAN_REPAYMENT"
    INSURANCE_PAYMENT = "INSURANCE_PAYMENT"
    OTHER = "OTHER"


class TransactionStatus(str, enum.Enum):
    COMPLETED = "COMPLETED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class FarmActivityType(str, enum.Enum):
    LAND_PREPARATION = "LAND_PREPARATION"
    PLANTING = "PLANTING"
    FERTILIZING = "FERTILIZING"
    SPRAYING = "SPRAYING"
    IRRIGATION = "IRRIGATION"
    WEEDING = "WEEDING"
    HARVESTING = "HARVESTING"
    OTHER = "OTHER"


class LoanStatus(str, enum.Enum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DISBURSED = "DISBURSED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    DEFAULTED = "DEFAULTED"


class LoanPurpose(str, enum.Enum):
    """What a farmer is asking the money for.

    A category rather than only free text: an officer deciding on an application,
    and any later reporting on where lending goes, both need to group the answers
    ("how many of these are for fertiliser?") without reading every sentence. The
    farmer's own words stay in ``LoanApplication.purpose``; this is the grouping.

    Deliberately the four uses a smallholder actually names - inputs, planting,
    machinery, and general money - plus OTHER, so nothing is forced into a
    category that does not fit. Adding one is a single line here and one
    translation string.
    """

    FERTILIZER = "FERTILIZER"
    DRILLING = "DRILLING"
    EQUIPMENT = "EQUIPMENT"
    MONEY = "MONEY"
    OTHER = "OTHER"


class IdentityMethod(str, enum.Enum):
    """How a person proved who they are. Only NIDA today; the enum is the seam
    a passport or driving-licence method would be added to."""

    NIDA = "NIDA"


class VerificationStatus(str, enum.Enum):
    """Lifecycle of one identity-verification session.

    PENDING  - a NIDA number was submitted and the provider has not answered
    VERIFIED - the provider confirmed the number; a phone OTP is still required
    FAILED   - the provider did not recognise the number
    EXPIRED  - the session outlived identity_session_ttl_minutes
    CONSUMED - the verification was spent creating an account, so it cannot be reused
    """

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class RiskLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class InsuranceProductType(str, enum.Enum):
    DROUGHT = "DROUGHT"
    EXCESS_RAINFALL = "EXCESS_RAINFALL"
    FLOOD = "FLOOD"
    CROP_YIELD = "CROP_YIELD"


class PolicyStatus(str, enum.Enum):
    """Where a policy is in its life.

    A farmer's request starts at PENDING_REVIEW and only becomes ACTIVE once an
    insurance officer has checked the farm qualifies - so cover, and the cheaper
    loan it unlocks, are never granted on the farmer's say-so alone. The column is
    a VARCHAR, so a new status needs no migration.
    """

    PENDING_REVIEW = "PENDING_REVIEW"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CLAIMED = "CLAIMED"


class ClaimStatus(str, enum.Enum):
    ELIGIBLE = "ELIGIBLE"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"


class EventStatus(str, enum.Enum):
    DETECTED = "DETECTED"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(180), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=32), default=UserRole.FARMER, nullable=False, index=True
    )
    preferred_language: Mapped[Language] = mapped_column(
        SAEnum(Language, native_enum=False, length=8), default=Language.EN, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    passkeys_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # WebAuthn/passkey-ready storage (credentials only - never raw biometrics)
    webauthn_credential_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webauthn_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    farmer: Mapped["Farmer | None"] = relationship(back_populates="user", uselist=False)
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Farmer(Base, TimestampMixin):
    __tablename__ = "farmers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    national_id_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    national_id_masked: Mapped[str | None] = mapped_column(String(12), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    age_range: Mapped[str | None] = mapped_column(String(16), nullable=True)
    village: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    cooperative_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    years_farming: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    savings_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_via: Mapped[str] = mapped_column(String(32), default="web", nullable=False)

    user: Mapped[User] = relationship(back_populates="farmer")
    farms: Mapped[list["Farm"]] = relationship(back_populates="farmer", cascade="all, delete-orphan")
    accounts: Mapped[list["FinancialAccount"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    loan_applications: Mapped[list["LoanApplication"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    insurance_policies: Mapped[list["InsurancePolicy"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["AiPrediction"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    insights: Mapped[list["AssistantInsight"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Farm production
# ---------------------------------------------------------------------------
class Farm(Base, TimestampMixin):
    __tablename__ = "farms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    village: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    area_acres: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    soil_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    irrigation_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    distance_to_market_km: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: the farmer's own photo of this farm, as a generated filename under
    #: static/uploads/farms. Only ever the name this app chose - never a path the
    #: client supplied - so a photo cannot be written outside that directory.
    photo_filename: Mapped[str | None] = mapped_column(String(80), nullable=True)

    farmer: Mapped[Farmer] = relationship(back_populates="farms")
    crops: Mapped[list["Crop"]] = relationship(back_populates="farm", cascade="all, delete-orphan")
    activities: Mapped[list["FarmActivity"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )
    policies: Mapped[list["InsurancePolicy"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("area_acres > 0", name="ck_farm_area_positive"),
    )


class Crop(Base, TimestampMixin):
    __tablename__ = "crops"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farm_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    crop_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    season: Mapped[str | None] = mapped_column(String(60), nullable=True)
    planting_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_harvest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_yield_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_yield_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    previous_yield_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    seed_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    fertilizer_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    labour_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    equipment_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    harvest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    farm: Mapped[Farm] = relationship(back_populates="crops")

    @property
    def production_cost(self) -> Decimal:
        return (self.seed_cost or 0) + (self.fertilizer_cost or 0) + (self.labour_cost or 0) + (
            self.equipment_cost or 0
        )


class FarmActivity(Base, TimestampMixin):
    __tablename__ = "farm_activities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farm_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("crops.id", ondelete="SET NULL"), nullable=True
    )
    activity_type: Mapped[FarmActivityType] = mapped_column(
        SAEnum(FarmActivityType, native_enum=False, length=32), nullable=False, index=True
    )
    activity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    labour_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    performed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    farm: Mapped[Farm] = relationship(back_populates="activities")


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
class FinancialAccount(Base, TimestampMixin):
    __tablename__ = "financial_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, native_enum=False, length=24), nullable=False, index=True
    )
    account_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    #: full number, encrypted at rest - needed to send a loan to this account
    account_number_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="TZS", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    farmer: Mapped[Farmer] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("financial_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, native_enum=False, length=32), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="in", nullable=False)  # in | out
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    transaction_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, native_enum=False, length=16), default=TransactionStatus.COMPLETED,
        nullable=False, index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    farmer: Mapped[Farmer] = relationship(back_populates="transactions")
    account: Mapped[FinancialAccount | None] = relationship(back_populates="transactions")

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        Index("ix_transactions_farmer_date", "farmer_id", "transaction_date"),
    )


# ---------------------------------------------------------------------------
# Credit
# ---------------------------------------------------------------------------
class LenderType(str, enum.Enum):
    SACCO = "SACCO"
    BANK = "BANK"
    MOBILE_MONEY = "MOBILE_MONEY"


class Lender(Base, TimestampMixin):
    """An institution a farmer can borrow from.

    Distinct from the payment providers in ``integrations.payments``: a payment
    provider moves money, a lender decides whether to lend it. The rows are
    published by a :class:`~app.integrations.lenders.base.LenderProvider`, so a
    real SACCO or bank integration can replace the demo ones without touching the
    tables or the matching logic.
    """

    __tablename__ = "lenders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: stable slug, e.g. "kilosa_sacco" - how a row is matched back to its provider
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    lender_type: Mapped[LenderType] = mapped_column(
        SAEnum(LenderType, native_enum=False, length=16), nullable=False, index=True
    )
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    min_loan_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    max_loan_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    typical_repayment_period_months: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    #: lenders that will only lend against an active parametric policy
    requires_insurance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    #: JSON list of regions this lender serves; empty = nationwide
    region_availability: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: the provider slug that published this row, e.g. "sacco_demo"
    provider_key: Mapped[str] = mapped_column(String(32), default="sacco_demo", nullable=False, index=True)
    #: demo rows are labelled as such everywhere they are shown
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    def regions(self) -> list[str]:
        if not self.region_availability:
            return []
        import json

        try:
            data = json.loads(self.region_availability)
        except (TypeError, ValueError):
            return []
        return [str(r) for r in data] if isinstance(data, list) else []


class LoanApplication(Base, TimestampMixin):
    __tablename__ = "loan_applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    farm_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("farms.id", ondelete="SET NULL"), nullable=True
    )
    crop_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("crops.id", ondelete="SET NULL"), nullable=True
    )
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    recommended_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    recommended_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=12, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    #: the grouped category the farmer picked, so applications can be counted and
    #: reported by use without reading the free text above
    purpose_code: Mapped[LoanPurpose | None] = mapped_column(
        SAEnum(LoanPurpose, native_enum=False, length=16), nullable=True, index=True
    )
    status: Mapped[LoanStatus] = mapped_column(
        SAEnum(LoanStatus, native_enum=False, length=24), default=LoanStatus.PENDING, nullable=False,
        index=True,
    )
    ai_risk_level: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=True
    )
    ai_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_prediction_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_via: Mapped[str] = mapped_column(String(16), default="web", nullable=False)
    #: which institution the farmer applied to; null means the platform's own offer
    lender_code: Mapped[str | None] = mapped_column(
        String(48), ForeignKey("lenders.code", ondelete="SET NULL"), nullable=True, index=True
    )

    farmer: Mapped[Farmer] = relationship(back_populates="loan_applications")
    loan: Mapped["Loan | None"] = relationship(back_populates="application", uselist=False)


class Loan(Base, TimestampMixin):
    __tablename__ = "loans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("loan_applications.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    outstanding_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[LoanStatus] = mapped_column(
        SAEnum(LoanStatus, native_enum=False, length=24), default=LoanStatus.ACTIVE, nullable=False,
        index=True,
    )
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    disbursement_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)

    application: Mapped[LoanApplication] = relationship(back_populates="loan")
    repayments: Mapped[list["LoanRepayment"]] = relationship(
        back_populates="loan", cascade="all, delete-orphan"
    )


class LoanRepayment(Base, TimestampMixin):
    __tablename__ = "loan_repayments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    loan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    principal_component: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    interest_component: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(32), default="MOBILE_MONEY", nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_on_time: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    loan: Mapped[Loan] = relationship(back_populates="repayments")


# ---------------------------------------------------------------------------
# Insurance
# ---------------------------------------------------------------------------
class InsuranceProduct(Base, TimestampMixin):
    __tablename__ = "insurance_products"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    name_sw: Mapped[str] = mapped_column(String(120), nullable=False)
    product_type: Mapped[InsuranceProductType] = mapped_column(
        SAEnum(InsuranceProductType, native_enum=False, length=32), nullable=False
    )
    description_en: Mapped[str] = mapped_column(Text, nullable=False)
    description_sw: Mapped[str] = mapped_column(Text, nullable=False)
    # parametric trigger configuration
    trigger_metric: Mapped[str] = mapped_column(String(40), default="rainfall_mm", nullable=False)
    trigger_operator: Mapped[str] = mapped_column(String(8), default="lt", nullable=False)
    trigger_threshold: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    trigger_window_start_day: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    trigger_window_end_day: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    premium_rate_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    sum_insured_per_acre: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InsurancePolicy(Base, TimestampMixin):
    __tablename__ = "insurance_policies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    policy_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    farm_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("crops.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("insurance_products.id", ondelete="RESTRICT"), nullable=False
    )
    premium_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    sum_insured: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[PolicyStatus] = mapped_column(
        SAEnum(PolicyStatus, native_enum=False, length=16), default=PolicyStatus.ACTIVE, nullable=False,
        index=True,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    farmer: Mapped[Farmer] = relationship(back_populates="insurance_policies")
    farm: Mapped[Farm] = relationship(back_populates="policies")
    product: Mapped[InsuranceProduct] = relationship()
    events: Mapped[list["InsuranceEvent"]] = relationship(back_populates="policy")
    claims: Mapped[list["InsuranceClaim"]] = relationship(back_populates="policy")


class InsuranceEvent(Base, TimestampMixin):
    __tablename__ = "insurance_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    policy_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[EventStatus] = mapped_column(
        SAEnum(EventStatus, native_enum=False, length=16), default=EventStatus.DETECTED, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="demo", nullable=False)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    policy: Mapped[InsurancePolicy] = relationship(back_populates="events")


class InsuranceClaim(Base, TimestampMixin):
    __tablename__ = "insurance_claims"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    claim_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("insurance_events.id", ondelete="SET NULL"), nullable=True
    )
    claim_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(
        SAEnum(ClaimStatus, native_enum=False, length=16), default=ClaimStatus.ELIGIBLE, nullable=False,
        index=True,
    )
    assessed_by_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    policy: Mapped[InsurancePolicy] = relationship(back_populates="claims")


# ---------------------------------------------------------------------------
# External data
# ---------------------------------------------------------------------------
class WeatherRecord(Base, TimestampMixin):
    __tablename__ = "weather_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    location: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rainfall_mm: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=0, nullable=False)
    temperature_max: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    temperature_min: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    humidity_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="demo", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("location", "record_date", "is_forecast", name="uq_weather_location_date"),
    )


class Buyer(Base, TimestampMixin):
    __tablename__ = "buyers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    buyer_type: Mapped[str] = mapped_column(String(40), default="COOPERATIVE", nullable=False)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    prices: Mapped[list["MarketPrice"]] = relationship(back_populates="buyer")


class MarketPrice(Base, TimestampMixin):
    __tablename__ = "market_prices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    crop_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    market_name: Mapped[str] = mapped_column(String(140), nullable=False, index=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="kg", nullable=False)
    price_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    buyer_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("buyers.id", ondelete="SET NULL"), nullable=True
    )
    demand_level: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    supply_level: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="demo", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    buyer: Mapped[Buyer | None] = relationship(back_populates="prices")

    __table_args__ = (
        Index("ix_market_crop_date", "crop_name", "price_date"),
    )


class BuyerOffer(Base, TimestampMixin):
    """A standing offer from a buyer: what crop, what price, how much, until when."""

    __tablename__ = "buyer_offers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    buyer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    max_quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    collection_point: Mapped[str | None] = mapped_column(String(160), nullable=True)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    buyer: Mapped[Buyer] = relationship()


class ListingStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    SOLD = "SOLD"
    WITHDRAWN = "WITHDRAWN"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class CropListing(Base, TimestampMixin):
    """A lot of produce a farmer offers for sale on the buyer marketplace.

    One listing is one sellable quantity of one crop. The price is what the
    farmer asks; buyers pay through the same sandbox payment rail every other
    AgriTrust payment uses.
    """

    __tablename__ = "crop_listings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    farm_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("farms.id", ondelete="SET NULL"), nullable=True
    )
    crop_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("crops.id", ondelete="SET NULL"), nullable=True
    )
    crop_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: price the farmer asks, per unit (kg)
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quality_grade: Mapped[str | None] = mapped_column(String(16), nullable=True)  # A | B | C
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ListingStatus] = mapped_column(
        SAEnum(ListingStatus, native_enum=False, length=16), default=ListingStatus.AVAILABLE,
        nullable=False, index=True,
    )

    farmer: Mapped[Farmer] = relationship()
    farm: Mapped["Farm | None"] = relationship()

    @property
    def asking_value(self) -> Decimal:
        return (self.quantity_kg or Decimal(0)) * (self.price_per_kg or Decimal(0))


class CropOrder(Base, TimestampMixin):
    """A buyer's purchase of (part of) one listing.

    The order is created PENDING_PAYMENT together with a PaymentRequest whose
    reference is the QR / pay-page key; when that request settles, the order
    becomes PAID and the listing is marked SOLD in the same transaction.
    """

    __tablename__ = "crop_orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    listing_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("crop_listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    buyer_user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, native_enum=False, length=20), default=OrderStatus.PENDING_PAYMENT,
        nullable=False, index=True,
    )
    payment_reference: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    listing: Mapped[CropListing] = relationship()
    buyer: Mapped[User] = relationship()


class LoanPayout(Base, TimestampMixin):
    """Where a loan is paid out to, and whether the provider confirmed the transfer."""

    __tablename__ = "loan_payouts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("loan_applications.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    farmer_id: Mapped[str] = mapped_column(String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("financial_accounts.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)  # MOBILE_MONEY | BANK
    account_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    account_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)  # PENDING | SENT | FAILED
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PaymentRequest(Base, TimestampMixin):
    """A payment the farmer is asked to make (loan instalment, savings deposit).

    The farmer pays to the provider's Lipa number quoting ``reference``, or scans
    the QR code, which opens the public ``/pay/<reference>`` page. In sandbox
    mode that page simulates the wallet confirmation; with a live provider the
    status moves to PAID from the provider's callback instead.
    """

    __tablename__ = "payment_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # LOAN_REPAYMENT | SAVINGS_DEPOSIT
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FarmAssessment(Base, TimestampMixin):
    """One transparent farm score: cash-flow estimate, 0-100 risk, explanation,
    a single recommended product, and the human review that makes it final.
    """

    __tablename__ = "farm_assessments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crop_name: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cash_flow_estimate: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0 (safe) .. 100 (risky)
    components: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: each factor, value, weight, why
    inputs: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: the figures the score was built from
    explanation_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_sw: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation_source: Mapped[str] = mapped_column(String(20), default="builtin", nullable=False)  # claude | builtin
    product_type: Mapped[str] = mapped_column(String(24), nullable=False)  # LOAN | INSURANCE_LOAN | GROUP_LENDING
    product_detail: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    review_status: Mapped[str] = mapped_column(String(20), default="PENDING_REVIEW", nullable=False, index=True)
    reviewed_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="web", nullable=False)  # web | ussd


class ReferralKind(str, enum.Enum):
    FINANCE = "FINANCE"
    INSURANCE = "INSURANCE"
    MARKET = "MARKET"


class Referral(Base, TimestampMixin):
    """A recommendation shown to a farmer, and whether they acted on it.

    This is what the impact dashboard counts: finance referrals, insurance
    referrals and market matches.
    """

    __tablename__ = "referrals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ReferralKind] = mapped_column(SAEnum(ReferralKind), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)  # buyer_offer | loan_application | insurance_policy
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_name: Mapped[str] = mapped_column(String(180), nullable=False)
    quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACCEPTED", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
class AiPrediction(Base, TimestampMixin):
    __tablename__ = "ai_predictions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    prediction_type: Mapped[str] = mapped_column(String(40), default="CREDIT_RISK", nullable=False, index=True)
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=False, index=True
    )
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    recommended_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    repayment_capacity: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    feature_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    weather_risk: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=True
    )
    market_risk: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=True
    )
    production_risk: Mapped[RiskLevel | None] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=True
    )
    metrics: Mapped[str | None] = mapped_column(Text, nullable=True)

    farmer: Mapped[Farmer] = relationship(back_populates="predictions")
    explanation: Mapped["AiExplanation | None"] = relationship(
        back_populates="prediction", uselist=False, cascade="all, delete-orphan"
    )


class AssistantInsight(Base, TimestampMixin):
    """One proactive recommendation the assistant offers a farmer without being asked.

    The same ``insight_key`` is refreshed in place on every run instead of piling up
    rows, so a farmer sees "heavy rain expected" once, not once per scan.
    """

    __tablename__ = "assistant_insights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: stable identifier of the rule that produced this, e.g. WEATHER_DRY_SPELL
    insight_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    #: WEATHER | MARKET | FINANCE | INSURANCE | HARVEST | DATA
    category: Mapped[str] = mapped_column(String(24), default="GENERAL", nullable=False, index=True)
    #: 1 = act today, 2 = plan this week, 3 = worth knowing
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False, index=True)
    #: INFO | ADVISORY | URGENT
    severity: Mapped[str] = mapped_column(String(12), default="ADVISORY", nullable=False)
    title_en: Mapped[str] = mapped_column(String(180), nullable=False)
    title_sw: Mapped[str] = mapped_column(String(180), nullable=False)
    body_en: Mapped[str] = mapped_column(Text, nullable=False)
    body_sw: Mapped[str] = mapped_column(Text, nullable=False)
    #: dashboard view this insight points at, e.g. "weather"
    action_view: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: a question the farmer can send to the assistant to hear more about this
    prompt_en: Mapped[str | None] = mapped_column(String(300), nullable=True)
    prompt_sw: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: the numbers behind the words, as JSON
    facts: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    #: when the farmer was last shown this insight (cleared again when the content changes)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    notified_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)

    farmer: Mapped[Farmer] = relationship(back_populates="insights")

    __table_args__ = (
        Index("ix_assistant_insight_farmer_key", "farmer_id", "insight_key", unique=True),
    )


class AiExplanation(Base, TimestampMixin):
    __tablename__ = "ai_explanations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    prediction_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ai_predictions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    factor_weights: Mapped[str | None] = mapped_column(Text, nullable=True)
    positive_factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    important_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(40), default="rules", nullable=False)

    prediction: Mapped[AiPrediction] = relationship(back_populates="explanation")


# ---------------------------------------------------------------------------
# Communication / governance
# ---------------------------------------------------------------------------
class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    farmer_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("farmers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(16), default="in_app", nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="general", nullable=False, index=True)
    title_en: Mapped[str] = mapped_column(String(180), nullable=False)
    title_sw: Mapped[str] = mapped_column(String(180), nullable=False)
    body_en: Mapped[str] = mapped_column(Text, nullable=False)
    body_sw: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    sent_via_sms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    farmer: Mapped[Farmer | None] = relationship(back_populates="notifications")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class SmsMessage(Base, TimestampMixin):
    __tablename__ = "sms_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="general", nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UssdSession(Base, TimestampMixin):
    __tablename__ = "ussd_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    state: Mapped[str] = mapped_column(String(48), default="language_select", nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class IdentityVerification(Base, TimestampMixin):
    """One identity-verification attempt, from the NIDA lookup to the new account.

    One row is one session, so a person who mistypes their NIDA number and tries
    again leaves a history rather than overwriting a decision.

    What is deliberately *not* here: the person's date of birth, gender, photo or
    address. A NIDA check only has to answer "is this number a real, registered
    identity", so nothing more is requested from the provider, received, or kept.
    """

    __tablename__ = "identity_verifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    #: set only once the verification is spent creating an account
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    method: Mapped[IdentityMethod] = mapped_column(
        SAEnum(IdentityMethod, native_enum=False, length=16),
        default=IdentityMethod.NIDA,
        nullable=False,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        SAEnum(VerificationStatus, native_enum=False, length=16),
        default=VerificationStatus.PENDING,
        nullable=False,
        index=True,
    )
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    identity_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- the national ID itself -------------------------------------------------
    #: the full number, encrypted at rest (app.security.crypto.encrypt_value).
    #: never selected into an API response and never written to a log.
    nida_number_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: keyed HMAC of the number. Lets us spot "this NIDA is already registered"
    #: by comparing hashes, with no need to decrypt anything.
    nida_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: last four digits, the only part ever shown or returned
    nida_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    #: how many digits the number has, so a masked value can reflect the real
    #: format rather than one hard-coded width
    nida_length: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- which provider answered, and honestly so -------------------------------
    provider_key: Mapped[str] = mapped_column(String(40), default="mock", nullable=False)
    #: True whenever the answer came from the test double rather than a real
    #: registry, so the UI can label the result instead of implying it is real.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: the provider's own reason, kept as a key the UI can translate
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- phone OTP --------------------------------------------------------------
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    phone_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    #: the one-time code, hashed - the code itself is never stored
    otp_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    otp_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    otp_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- lifetime ---------------------------------------------------------------
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # one NIDA can back exactly one account
        Index("ux_identity_nida_hash", "nida_hash", unique=True),
    )

