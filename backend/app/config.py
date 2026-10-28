"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "AgriTrust AI"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "dev-secret-key-change-me"
    api_v1_prefix: str = "/api/v1"
    frontend_dir: Path = BASE_DIR / "frontend"
    locales_dir: Path = BASE_DIR / "frontend" / "locales"
    ml_model_dir: Path = BASE_DIR / "ml" / "credit_risk"

    # Database (MariaDB / MySQL)
    database_url: str = "mysql+pymysql://agritrust:agritrust_dev_pw@localhost:3306/agritrust?charset=utf8mb4"
    test_database_url: str = "mysql+pymysql://agritrust:agritrust_dev_pw@localhost:3306/agritrust_test?charset=utf8mb4"

    # Security
    jwt_secret_key: str = "dev-jwt-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14
    login_rate_limit: int = 10
    password_min_length: int = 8
    data_encryption_key: str = "dev-data-encryption-key"

    cors_origins: List[str] | str = ["http://localhost:8000", "http://127.0.0.1:8000"]

    # Demo / mocking
    mock_mode: bool = True
    demo_mode: bool = True

    # Africa's Talking (SMS + USSD)
    africastalking_username: str = "sandbox"
    africastalking_api_key: str = "sandbox"
    # empty = the gateway's default sender; set only an alphanumeric sender id
    # (or short code) that is registered on your Africa's Talking account
    africastalking_sender_id: str = ""
    africastalking_env: str = "sandbox"
    africastalking_ussd_code: str = "*XYZ#"
    africastalking_shortcode: str = ""
    africastalking_callback_url: str = "https://example.ngrok.app/api/v1/ussd/callback"
    # optional shared secret; when set, callbacks must carry ?token=<value>
    africastalking_callback_token: str = ""

    # Weather
    weather_provider: str = "mock"
    openmeteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_api_key: str = ""

    # Market
    market_provider: str = "mock"
    market_api_url: str = ""
    market_api_key: str = ""

    # Payments
    payment_provider: str = "mock"
    africastalking_payment_product: str = "Telecom"
    africastalking_payment_target: str = "+255000000000"
    africastalking_payment_currency: str = "TZS"

    # LLM
    llm_provider: str = "offline"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30
    llm_temperature: float = 0.2
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # ML
    credit_risk_min_train_samples: int = 150

    # --- Identity verification (NIDA) ---
    # Which provider answers "is this national ID real?". "mock" is the clearly
    # labelled test double; "nida" is the real adapter and refuses to run unless
    # it has been given a base URL and a key, so it can never quietly pretend.
    identity_provider: str = "mock"            # mock | nida
    #: refuse to create an account without a verified identity
    identity_required: bool = True
    #: Which roles must verify a national ID, comma separated. A farmer is
    #: transacting under their own name and is the identity that matters on the
    #: platform, so FARMER is the default. A buyer or an officer acting for an
    #: institution is onboarded by that institution instead, and forcing a NIDA
    #: on them would be demanding an identity the onboarding process never uses.
    #: ``identity_required`` stays the master switch: with it off, no role needs one.
    identity_required_roles: str = "FARMER"
    #: Accepted NIDA lengths, comma separated. Tanzania has issued more than one
    #: format: 16 digits for IDs from the legacy scheme, 20 for the current one
    #: (birth date, then a sequence). A list rather than a single number, because
    #: a real registry has to accept every format it ever issued - and because
    #: pinning one length silently rejects real citizens.
    identity_nida_lengths: str = "16,20"
    #: the real NIDA adapter - unused while identity_provider is "mock"
    nida_api_base_url: str = ""
    nida_api_key: str = ""
    nida_timeout_seconds: int = 20
    #: NIDA lookups allowed per identity_verify_rate_window_seconds, per IP and
    #: per phone number. Guards against enumerating the registry one number at a time.
    identity_verify_rate_limit: int = 5
    identity_verify_rate_window_seconds: int = 600
    #: phone OTP lifetime, and how wrong codes are tolerated before the session dies
    identity_otp_ttl_seconds: int = 300
    identity_otp_max_attempts: int = 5
    #: how long a verified identity stays usable before the account must be created
    identity_session_ttl_minutes: int = 30
    #: lifetime of the ticket the browser hands back to /auth/register
    identity_ticket_ttl_minutes: int = 15

    # --- Proactive assistant ---
    # The assistant refreshes every farmer's recommendations on a timer and pushes
    # the new ones, so a farmer is helped without having to ask anything.
    proactive_enabled: bool = True
    #: minutes between two refresh passes
    proactive_interval_minutes: int = 30
    #: wait this long after startup so the first pass never competes with page loads
    proactive_initial_delay_seconds: int = 20
    #: farmers refreshed per pass, so a big database never blocks a tick
    proactive_batch_size: int = 25
    #: stop a pass once it has taken this long, and pick up where it stopped next time
    proactive_pass_budget_seconds: float = 20.0
    #: a farmer's insights are recomputed on the fly when older than this
    proactive_max_age_minutes: int = 45
    #: only insights this urgent (or more urgent) create a notification
    proactive_notify_max_priority: int = 1
    #: and never more than this many per farmer in a single pass
    proactive_notify_max_per_pass: int = 2
    #: how often the browser asks the server for anything new (seconds)
    proactive_client_poll_seconds: int = 60

    # --- Payments ---
    # address phones can reach, used inside payment QR codes (e.g. http://192.168.1.20:8000);
    # empty = the address the page was requested on
    public_base_url: str = ""
    payment_request_minutes: int = 30

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def nida_lengths(self) -> tuple[int, ...]:
        """The accepted NIDA lengths, shortest first.

        Parsed from ``identity_nida_lengths``; anything unparseable is ignored,
        and a setting that parses to nothing falls back to the two documented
        formats rather than rejecting every number.
        """
        out: list[int] = []
        for part in (self.identity_nida_lengths or "").split(","):
            part = part.strip()
            if part.isdigit() and 1 < int(part) < 64:
                value = int(part)
                if value not in out:
                    out.append(value)
        return tuple(sorted(out)) or (16, 20)

    @property
    def identity_roles(self) -> frozenset[str]:
        """The roles that must verify a national ID, upper-cased.

        Parsed from ``identity_required_roles``. Anything that is not a real role
        is dropped rather than trusted, and a setting left with nothing usable
        falls back to FARMER - so a typo in the environment can never quietly
        drop the requirement for everyone.
        """
        from app.models import UserRole  # local: models does not import config

        known = {role.value for role in UserRole}
        out: set[str] = set()
        for part in (self.identity_required_roles or "").split(","):
            part = part.strip().upper()
            if part in known:
                out.add(part)
        return frozenset(out) or frozenset({"FARMER"})

    def identity_required_for(self, role: object) -> bool:
        """Whether this registration role has to verify a national ID.

        The master switch wins: with ``identity_required`` off no role is gated.
        """
        if not self.identity_required:
            return False
        name = getattr(role, "value", role)
        return str(name or "").upper() in self.identity_roles

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_mysql(self) -> bool:
        return self.database_url.startswith("mysql")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
