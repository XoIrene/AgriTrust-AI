"""Pydantic schemas for request/response validation."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import (
    AccountType,
    FarmActivityType,
    InsuranceProductType,
    Language,
    LoanPurpose,
    LoanStatus,
    PolicyStatus,
    RiskLevel,
    TransactionStatus,
    TransactionType,
    UserRole,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Identity verification
# ---------------------------------------------------------------------------
class NidaVerifyRequest(BaseModel):
    """Ask the configured provider whether a national ID is real.

    ``full_name`` is accepted and ignored by the format check; the provider may
    echo a name back, but this application does not enforce a name match - that
    is a policy decision for whoever owns the NIDA agreement.
    """

    nida_number: str = Field(min_length=4, max_length=32)
    full_name: Optional[str] = Field(default=None, max_length=160)


class IdentityMethodOut(BaseModel):
    key: str
    label: str
    field_label_key: str
    placeholder_key: str
    action_key: str
    hint_key: str
    #: every digit length the method accepts
    expected_lengths: list[int]
    #: True when answers come from a test double, so the page must label it
    is_sandbox: bool
    available: bool
    unavailable_reason: Optional[str] = None
    active: bool


class IdentityDemoNumber(BaseModel):
    """One invented number, offered as a hint while the test double is active."""

    number: str
    full_name: Optional[str] = None
    verifies: bool


class IdentityConfigOut(BaseModel):
    """What the registration page needs before it renders the section."""

    methods: list[IdentityMethodOut]
    required: bool
    #: True when the active provider is the test double
    is_sandbox: bool
    provider_key: Optional[str] = None
    #: invented numbers to try, present only while is_sandbox is true
    demo_numbers: list[IdentityDemoNumber] = []


class NidaVerifyOut(BaseModel):
    """The result of a NIDA lookup.

    Carries ``nida_masked`` (last four digits only) and never the number, so the
    browser can show what it checked without the full ID travelling back out.
    ``verification_id`` is an opaque session handle - the phone OTP and the
    registration ticket are both keyed off it, and it reveals nothing on its own.
    """

    verified: bool
    verification_status: str
    verification_id: Optional[str] = None
    nida_masked: str
    #: translation key for a failure, absent on success
    reason: Optional[str] = None
    #: True when this came from the test double rather than a real registry
    is_sandbox: bool
    provider_key: str
    #: present only once the phone OTP has also been confirmed
    phone_verified: bool = False
    #: single-use proof of verification, to be sent to /auth/register
    identity_ticket: Optional[str] = None
    expires_in_seconds: Optional[int] = None
    #: seconds until the OTP may be requested again; absent when it may be now
    retry_after: Optional[int] = None


class PhoneOtpRequest(BaseModel):
    #: the verification session, from the NIDA lookup response
    verification_id: str = Field(min_length=8, max_length=32)
    phone: str = Field(min_length=6, max_length=32)


class PhoneOtpVerifyRequest(BaseModel):
    verification_id: str = Field(min_length=8, max_length=32)
    code: str = Field(min_length=4, max_length=8)


class PhoneOtpRequestOut(BaseModel):
    sent: bool
    #: translation key explaining why not, when ``sent`` is false
    reason: Optional[str] = None
    #: seconds until another code may be requested
    retry_after: Optional[int] = None
    is_sandbox: bool = True
    #: how many digits the code has, so the page never has to guess
    otp_digits: int = 6
    #: digits of the code, delivered out of band by the mock SMS provider so a
    #: developer can complete the flow. Empty for any real provider.
    dev_code: Optional[str] = None


class PhoneOtpVerifyOut(BaseModel):
    verified: bool
    phone_verified: bool
    nida_masked: str
    identity_ticket: Optional[str] = None
    reason: Optional[str] = None
    expires_in_seconds: Optional[int] = None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=6, max_length=32)
    email: Optional[EmailStr] = None
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.FARMER
    preferred_language: Language = Language.EN
    # farmer extras (optional, only used for FARMER role)
    village: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None
    #: proof of a verified identity, handed back by /identity/nida/verify then
    #: /identity/otp/verify. Required while settings.identity_required is on -
    #: this is what stops a caller skipping verification by posting here directly.
    #: There is deliberately no nida_number field: the number is looked up by the
    #: ticket, so it can never be supplied, forged or swapped at this endpoint.
    identity_ticket: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "")
        if not v.replace("+", "").isdigit():
            raise ValueError("phone must be numeric")
        return v

    @field_validator("full_name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return " ".join(v.split())


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, description="phone number or email")
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(ORMModel):
    id: str
    full_name: str
    email: Optional[str] = None
    phone: str
    role: UserRole
    preferred_language: Language
    is_active: bool
    last_login_at: Optional[datetime] = None


class MessageOut(BaseModel):
    message: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Farmer
# ---------------------------------------------------------------------------
class FarmerUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    gender: Optional[str] = None
    age_range: Optional[str] = None
    village: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None
    national_id: Optional[str] = None
    cooperative_name: Optional[str] = None
    years_farming: Optional[int] = Field(default=None, ge=0, le=80)
    savings_balance: Optional[Decimal] = Field(default=None, ge=0)
    preferred_language: Optional[Language] = None


class FarmerOut(ORMModel):
    id: str
    user_id: str
    village: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None
    gender: Optional[str] = None
    age_range: Optional[str] = None
    cooperative_name: Optional[str] = None
    years_farming: int
    savings_balance: Decimal
    national_id_masked: Optional[str] = None
    onboarding_complete: bool
    created_via: str


class FarmerDetail(FarmerOut):
    user: UserOut
    farm_count: int = 0
    total_area_acres: float = 0.0


# ---------------------------------------------------------------------------
# Farm / crop / activity
# ---------------------------------------------------------------------------
class FarmCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    village: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None
    area_acres: Decimal = Field(gt=0, le=10000)
    soil_type: Optional[str] = None
    irrigation_available: bool = False
    distance_to_market_km: Decimal = Field(default=0, ge=0)


class FarmUpdate(BaseModel):
    name: Optional[str] = None
    village: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None
    area_acres: Optional[Decimal] = Field(default=None, gt=0, le=10000)
    soil_type: Optional[str] = None
    irrigation_available: Optional[bool] = None
    distance_to_market_km: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class FarmOut(ORMModel):
    id: str
    name: str
    village: Optional[str] = None
    district: Optional[str] = None
    region: Optional[str] = None
    area_acres: Decimal
    soil_type: Optional[str] = None
    irrigation_available: bool
    distance_to_market_km: Decimal
    is_active: bool
    crop_count: int = 0
    current_crop: Optional[str] = None
    #: where the farmer's own photo of this farm can be fetched, or None
    photo_url: Optional[str] = None


class CropCreate(BaseModel):
    crop_name: str = Field(min_length=2, max_length=80)
    crop_category: Optional[str] = None
    season: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    expected_yield_kg: Optional[Decimal] = Field(default=None, ge=0)
    previous_yield_kg: Optional[Decimal] = Field(default=None, ge=0)
    seed_cost: Decimal = Field(default=0, ge=0)
    fertilizer_cost: Decimal = Field(default=0, ge=0)
    labour_cost: Decimal = Field(default=0, ge=0)
    equipment_cost: Decimal = Field(default=0, ge=0)
    is_current: bool = True


class CropUpdate(BaseModel):
    crop_name: Optional[str] = None
    crop_category: Optional[str] = None
    season: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    expected_yield_kg: Optional[Decimal] = Field(default=None, ge=0)
    actual_yield_kg: Optional[Decimal] = Field(default=None, ge=0)
    seed_cost: Optional[Decimal] = Field(default=None, ge=0)
    fertilizer_cost: Optional[Decimal] = Field(default=None, ge=0)
    labour_cost: Optional[Decimal] = Field(default=None, ge=0)
    equipment_cost: Optional[Decimal] = Field(default=None, ge=0)
    is_current: Optional[bool] = None


class CropOut(ORMModel):
    id: str
    farm_id: str
    crop_name: str
    crop_category: Optional[str] = None
    season: Optional[str] = None
    planting_date: Optional[date] = None
    expected_harvest_date: Optional[date] = None
    expected_yield_kg: Optional[Decimal] = None
    actual_yield_kg: Optional[Decimal] = None
    previous_yield_kg: Optional[Decimal] = None
    seed_cost: Decimal
    fertilizer_cost: Decimal
    labour_cost: Decimal
    equipment_cost: Decimal
    is_current: bool
    production_cost: Decimal = Decimal("0")


class ActivityCreate(BaseModel):
    activity_type: FarmActivityType
    activity_date: date
    cost: Decimal = Field(default=0, ge=0)
    crop_id: Optional[str] = None
    description: Optional[str] = None
    labour_count: int = Field(default=0, ge=0)
    performed_by: Optional[str] = None


class ActivityOut(ORMModel):
    id: str
    farm_id: str
    crop_id: Optional[str] = None
    activity_type: FarmActivityType
    activity_date: date
    cost: Decimal
    description: Optional[str] = None
    labour_count: int
    performed_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
class AccountCreate(BaseModel):
    provider: str = Field(min_length=2, max_length=80)
    account_type: AccountType
    account_number: str = Field(min_length=4, max_length=40)
    account_name: Optional[str] = None
    balance: Decimal = Field(default=0, ge=0)
    is_primary: bool = False
    is_sandbox: bool = True


class AccountOut(ORMModel):
    id: str
    provider: str
    account_type: AccountType
    account_number_masked: str
    account_name: Optional[str] = None
    balance: Decimal
    currency: str
    is_primary: bool
    is_sandbox: bool


class TransactionCreate(BaseModel):
    account_id: Optional[str] = None
    provider: Optional[str] = None
    transaction_type: TransactionType
    amount: Decimal = Field(gt=0)
    transaction_date: Optional[datetime] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    status: TransactionStatus = TransactionStatus.COMPLETED


class TransactionOut(ORMModel):
    id: str
    farmer_id: str
    account_id: Optional[str] = None
    provider: str
    transaction_type: TransactionType
    amount: Decimal
    direction: str
    reference: Optional[str] = None
    transaction_date: datetime
    status: TransactionStatus
    description: Optional[str] = None
    is_sandbox: bool


class FinancialProfileOut(BaseModel):
    period_months: int
    monthly_income: float
    monthly_expenses: float
    monthly_net: float
    savings_rate: float
    transaction_count: int
    transactions_per_month: float
    average_transaction: float
    farm_income_monthly: float
    farm_expenses_monthly: float
    crop_sale_income_total: float
    loan_repayments_total: float
    on_time_repayment_rate: float
    total_balance: float
    financial_health: str
    monthly_series: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------
class LoanApplyRequest(BaseModel):
    farm_id: Optional[str] = None
    crop_id: Optional[str] = None
    requested_amount: Decimal = Field(gt=0, le=100_000_000)
    #: the grouped use the farmer picked. Optional so an older client, or the USSD
    #: path, can still apply with words only - but the web form always sends one.
    purpose_code: Optional[LoanPurpose] = None
    #: the farmer's own words about the loan. Optional when a category was picked,
    #: because forcing ten characters of typing on top of a category is friction;
    #: the endpoint then records the category as the purpose. One of the two is
    #: always required, so an application can never be purpose-less.
    purpose: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _needs_a_purpose(self) -> "LoanApplyRequest":
        if self.purpose_code is not None:
            return self
        if len(self.purpose.strip()) < 10:
            raise ValueError("loan.purpose_needed")
        return self
    term_months: int = Field(default=12, ge=1, le=60)
    # which institution to borrow from; the officer sees that lender's rate
    lender_code: Optional[str] = Field(default=None, max_length=48)
    # where the money goes: a saved account, or a new provider + number
    payout_account_id: Optional[str] = None
    payout_provider: Optional[str] = None
    payout_account_number: Optional[str] = Field(default=None, max_length=40)
    payout_account_name: Optional[str] = Field(default=None, max_length=160)


class PayoutRequest(BaseModel):
    payout_account_id: Optional[str] = None
    payout_provider: Optional[str] = None
    payout_account_number: Optional[str] = Field(default=None, max_length=40)
    payout_account_name: Optional[str] = Field(default=None, max_length=160)


class LoanDecision(BaseModel):
    reason: Optional[str] = None
    approved_amount: Optional[Decimal] = Field(default=None, gt=0)
    #: an officer may fix the institution, rate and term at decision time
    lender_code: Optional[str] = Field(default=None, max_length=48)
    interest_rate: Optional[Decimal] = Field(default=None, gt=0, le=100)
    term_months: Optional[int] = Field(default=None, ge=1, le=60)
    #: free-text covenant / condition attached to the approval
    conditions: Optional[str] = Field(default=None, max_length=2000)


class AssignLenderRequest(BaseModel):
    """Bind an application to one financial institution before it is decided."""

    lender_code: str = Field(min_length=1, max_length=48)
    interest_rate: Optional[Decimal] = Field(default=None, gt=0, le=100)
    term_months: Optional[int] = Field(default=None, ge=1, le=60)


class OfficerNoteRequest(BaseModel):
    note: str = Field(min_length=2, max_length=2000)


class RepaymentReminderRequest(BaseModel):
    channel: str = Field(default="BOTH", pattern="^(SMS|IN_APP|BOTH)$")
    message: Optional[str] = Field(default=None, max_length=480)


class RepaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_date: Optional[date] = None
    method: str = "MOBILE_MONEY"
    reference: Optional[str] = None
    is_on_time: bool = True


class LoanOut(ORMModel):
    id: str
    application_id: str
    reference: str
    farmer_id: str
    farmer_name: Optional[str] = None
    requested_amount: Decimal
    purpose: str
    #: the grouped use the farmer picked; None on applications created before the
    #: category existed, which is why it stays optional all the way through
    purpose_code: Optional[LoanPurpose] = None
    status: LoanStatus
    ai_risk_level: Optional[RiskLevel] = None
    ai_risk_score: Optional[float] = None
    recommended_min: Optional[Decimal] = None
    recommended_max: Optional[Decimal] = None
    term_months: int
    interest_rate: Decimal
    applied_on: datetime
    lender_code: Optional[str] = None
    decision_reason: Optional[str] = None
    decided_at: Optional[datetime] = None


class LoanDetail(LoanOut):
    farm_name: Optional[str] = None
    crop_name: Optional[str] = None
    loan_principal: Optional[Decimal] = None
    outstanding_balance: Optional[Decimal] = None
    monthly_payment: Optional[Decimal] = None
    due_date: Optional[date] = None
    repaid_amount: Decimal = Decimal("0")
    repayments: list["RepaymentOut"] = []
    payout: Optional[dict[str, Any]] = None


class RepaymentOut(ORMModel):
    id: str
    amount: Decimal
    principal_component: Decimal
    interest_component: Decimal
    payment_date: date
    method: str
    reference: Optional[str] = None
    is_on_time: bool


# ---------------------------------------------------------------------------
# Insurance
# ---------------------------------------------------------------------------
class PolicyCreate(BaseModel):
    farm_id: str
    crop_id: Optional[str] = None
    product_code: str
    start_date: Optional[date] = None
    term_months: int = Field(default=6, ge=1, le=24)


class ProductOut(ORMModel):
    id: str
    code: str
    name_en: str
    name_sw: str
    product_type: InsuranceProductType
    description_en: str
    description_sw: str
    trigger_metric: str
    trigger_operator: str
    trigger_threshold: Decimal
    trigger_window_start_day: int
    trigger_window_end_day: int
    premium_rate_pct: Decimal
    sum_insured_per_acre: Decimal


class PolicyOut(ORMModel):
    id: str
    policy_number: str
    farmer_id: str
    farm_id: str
    crop_id: Optional[str] = None
    product_id: str
    product_name_en: str = ""
    product_name_sw: str = ""
    premium_paid: Decimal
    sum_insured: Decimal
    status: PolicyStatus
    start_date: date
    end_date: date
    is_simulation: bool
    #: farm the policy covers, so the farmer does not have to match ids themselves
    farm_name: Optional[str] = None
    #: who asked for the cover - an insurance officer has to know before approving
    farmer_name: Optional[str] = None
    #: how far the parametric trigger has moved; ``available`` is False when the
    #: stored records cannot support a number (never a guess)
    trigger: Optional[dict[str, Any]] = None


class ClaimOut(ORMModel):
    id: str
    claim_number: str
    policy_id: str
    event_id: Optional[str] = None
    claim_amount: Decimal
    status: str
    decision_note: Optional[str] = None
    is_simulation: bool


class EventOut(ORMModel):
    id: str
    policy_id: str
    event_type: str
    observed_value: Decimal
    threshold_value: Decimal
    location: Optional[str] = None
    period_start: date
    period_end: date
    status: str
    description: Optional[str] = None
    source: str
    is_simulation: bool


class ClaimDecision(BaseModel):
    status: str = Field(pattern="^(UNDER_REVIEW|APPROVED|REJECTED|PAID)$")
    decision_note: Optional[str] = None


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
class CreditRiskRequest(BaseModel):
    farmer_id: Optional[str] = None
    persist: bool = True


class FactorOut(BaseModel):
    key: str
    label: str
    weight: float
    direction: str
    value: float | None = None
    impact: float = 0.0


class ExplanationOut(BaseModel):
    language: str
    summary: str
    factor_weights: list[FactorOut] = []
    positive_factors: list[dict[str, Any]] = []
    risk_factors: list[dict[str, Any]] = []


class CreditRiskOut(BaseModel):
    prediction_id: Optional[str] = None
    model_name: str
    model_version: str
    risk_level: RiskLevel
    risk_score: float
    confidence: float | None
    credit_risk: RiskLevel
    weather_risk: RiskLevel
    market_risk: RiskLevel
    production_risk: RiskLevel
    recommended_min: Optional[Decimal]
    recommended_max: Optional[Decimal]
    repayment_capacity: Optional[Decimal]
    explanation: Optional[ExplanationOut] = None
    human_review_required: bool = True
    features: dict[str, Any] = {}
    data_quality: Optional[dict[str, Any]] = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    language: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    language: str
    data_sources: list[str] = []
    used_llm: bool = False
    suggestions: list[str] = []


# ---------------------------------------------------------------------------
# Proactive assistant
# ---------------------------------------------------------------------------
class InsightOut(BaseModel):
    id: str
    key: str
    category: str
    priority: int
    severity: str
    title: str
    body: str
    action_view: Optional[str] = None
    prompt: Optional[str] = None
    valid_until: Optional[str] = None
    is_read: bool = False
    facts: dict[str, Any] = {}
    generated_at: Optional[str] = None


class InsightListOut(BaseModel):
    insights: list[InsightOut] = []
    unread: int = 0
    language: str = "en"
    generated_at: Optional[str] = None
    next_check_in_seconds: int = 60
    questions: list[str] = []


class InsightActionOut(BaseModel):
    ok: bool = True
    insight_id: str
    is_read: Optional[bool] = None
    dismissed: Optional[bool] = None


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class DashboardStats(BaseModel):
    total_farmers: int
    active_farms: int
    loan_applications: int
    active_loans: int
    total_financing: Decimal
    insurance_policies: int
    insurance_events: int
    farmers_at_risk: int
    pending_reviews: int
    total_transactions: int
    total_credit_disbursed: Decimal
    total_claims_paid: Decimal


TokenResponse.model_rebuild()
LoanDetail.model_rebuild()
