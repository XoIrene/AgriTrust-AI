"""Authentication endpoints: register, login, refresh, logout, me."""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import client_ip, get_current_user
from app.i18n import translate
from app.models import Farmer, Language, RefreshToken, User, UserRole, utcnow
from app.schemas import (
    LoginRequest,
    MessageOut,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.security.crypto import (
    create_access_token,
    create_refresh_token,
    hash_token,
    hash_password,
    password_strength_errors,
    verify_password,
)
from app.services import audit
from app.services.identity import (
    IdentityFlowError,
    consume_verification,
    redeem_registration_ticket,
)
from app.services.sms import send_welcome

logger = logging.getLogger("agritrust.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
SELF_REGISTER_FORBIDDEN = {UserRole.ADMIN, UserRole.SUPER_ADMIN}


def _issue_tokens(db: Session, user: User, request: Request) -> TokenResponse:
    access = create_access_token(user.id, user.role.value)
    refresh, refresh_hash, expires = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=expires,
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    user.last_login_at = utcnow()
    user.failed_login_attempts = 0
    db.commit()
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    # admins are never self-registered; officer sign-up is a demo convenience only
    # farmers and buyers sign up freely; officers only in demo mode; never admins
    if payload.role in SELF_REGISTER_FORBIDDEN or (
        payload.role not in (UserRole.FARMER, UserRole.BUYER) and not settings.demo_mode
    ):
        raise HTTPException(status_code=403, detail="error.forbidden_role")

    # --- identity gate -------------------------------------------------------
    # Checked before anything is written, so a caller that posts here without a
    # verified identity creates nothing at all. The ticket names the verification
    # session, which is where the national ID lives: there is no nida_number field
    # on this request, so the number cannot be supplied or swapped from outside.
    #
    # Only the roles in IDENTITY_REQUIRED_ROLES are gated - a farmer signs up as
    # themselves, while a buyer or an officer is onboarded by the institution they
    # act for, so demanding a national ID there would block a legitimate signup for
    # an identity the onboarding process does not use.
    identity_needed = settings.identity_required_for(payload.role)
    verification = None
    if identity_needed:
        try:
            verification = redeem_registration_ticket(
                db, payload.identity_ticket or "", phone=payload.phone
            )
        except IdentityFlowError as exc:
            audit.log_action(
                db,
                action="auth.register_blocked",
                details={"reason": exc.reason, "phone": payload.phone[-4:]},
                ip_address=client_ip(request),
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.reason,
                headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None,
            ) from exc

    # duplicate check on email / phone
    if payload.email:
        existing = db.execute(select(User).where(User.email == payload.email)).first()
        if existing:
            raise HTTPException(status_code=409, detail="auth.email_taken")
    existing = db.execute(select(User).where(User.phone == payload.phone)).first()
    if existing:
        raise HTTPException(status_code=409, detail="auth.phone_taken")

    errors = password_strength_errors(payload.password)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"password": [translate(key) for key in errors]},
        )

    user = User(
        email=payload.email or f"{payload.phone}@agritrust.local",
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        preferred_language=payload.preferred_language,
        is_active=True,
        # a farmer who proved their identity starts verified
        is_verified=bool(verification and verification.identity_verified),
    )
    db.add(user)
    db.flush()

    if payload.role == UserRole.FARMER:
        farmer = Farmer(
            user_id=user.id,
            village=payload.village,
            district=payload.district,
            region=payload.region,
            created_via="web",
        )
        db.add(farmer)
        db.flush()
        audit.log_action(
            db,
            action="farmer.register",
            user_id=user.id,
            actor_role=user.role.value,
            entity_type="farmer",
            entity_id=farmer.id,
            details={"phone": payload.phone[-4:]},
            ip_address=client_ip(request),
        )
        send_welcome(db, farmer)

    # spend the verification last, so a failure above leaves it usable
    if verification is not None:
        consume_verification(db, verification, user)
        audit.log_action(
            db,
            action="identity.consumed",
            user_id=user.id,
            actor_role=user.role.value,
            entity_type="identity_verification",
            entity_id=verification.id,
            details={"sandbox": bool(verification.is_sandbox)},
            ip_address=client_ip(request),
        )

    db.commit()
    db.refresh(user)
    return _issue_tokens(db, user, request)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.execute(
        select(User).where(or_(User.phone == payload.identifier, User.email == payload.identifier))
    ).scalars().first()
    if user is None:
        audit.log_action(
            db, action="auth.login_failed", details={"identifier": "unknown"}, ip_address=client_ip(request)
        )
        raise HTTPException(status_code=401, detail="auth.invalid_credentials")

    if user.locked_until and user.locked_until > utcnow():
        raise HTTPException(status_code=423, detail="auth.account_locked")

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
        db.commit()
        audit.log_action(
            db,
            action="auth.login_failed",
            user_id=user.id,
            actor_role=user.role.value,
            ip_address=client_ip(request),
        )
        raise HTTPException(status_code=401, detail="auth.invalid_credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="account_disabled")

    audit.log_action(
        db,
        action="auth.login",
        user_id=user.id,
        actor_role=user.role.value,
        ip_address=client_ip(request),
    )
    return _issue_tokens(db, user, request)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    from app.security.crypto import decode_token
    import jwt

    try:
        data = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="auth.invalid_credentials")

    stored = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(payload.refresh_token))
    ).scalars().first()
    if stored is None or stored.revoked or stored.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="auth.invalid_credentials")

    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="auth.invalid_credentials")

    stored.revoked = True
    db.commit()
    audit.log_action(db, action="auth.refresh", user_id=user.id, actor_role=user.role.value)
    return _issue_tokens(db, user, request)


@router.post("/logout", response_model=MessageOut)
def logout(
    payload: RefreshRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    if payload and payload.refresh_token:
        stored = db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(payload.refresh_token))
        ).scalars().first()
        if stored and stored.user_id == user.id:
            stored.revoked = True
    db.execute(
        RefreshToken.__table__.update()
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    db.commit()
    audit.log_action(db, action="auth.logout", user_id=user.id, actor_role=user.role.value)
    return MessageOut(message=translate("auth.logout_success"))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me/language", response_model=UserOut)
def set_language(
    language: Language,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    user.preferred_language = language
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)
