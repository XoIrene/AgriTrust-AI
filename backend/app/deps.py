"""FastAPI dependencies: current user, role-based access control, i18n."""
from __future__ import annotations

from typing import Callable, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.i18n import get_language, negotiate_language
from app.models import Farmer, User, UserRole
from app.security.crypto import decode_token

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid_credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    negotiate_language(request)
    if credentials is None or not credentials.credentials:
        raise CREDENTIALS_ERROR
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.PyJWTError:
        raise CREDENTIALS_ERROR
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise CREDENTIALS_ERROR
    request.state.user = user
    return user


def get_current_farmer(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Farmer:
    farmer = db.query(Farmer).filter(Farmer.user_id == user.id).first()
    if farmer is None:
        if user.role == UserRole.FARMER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="farmer_profile_incomplete",
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_a_farmer")
    return farmer


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Dependency factory enforcing role-based access control."""
    allowed = set(roles)

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="forbidden_role",
            )
        return user

    return _dependency


require_admin = require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)
require_loan_officer = require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)
require_insurance_officer = require_roles(
    UserRole.INSURANCE_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN
)
require_cooperative = require_roles(
    UserRole.COOPERATIVE_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN
)
require_officer = require_roles(
    UserRole.LOAN_OFFICER,
    UserRole.INSURANCE_OFFICER,
    UserRole.COOPERATIVE_OFFICER,
    UserRole.ADMIN,
    UserRole.SUPER_ADMIN,
)


#: roles that legitimately work across every farmer's records: they do it as
#: staff, on an underwriting or underwriting-review desk. A BUYER is deliberately
#: not here - they buy crops, they do not administer farms.
CROSS_FARM_ROLES = frozenset(
    {
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.LOAN_OFFICER,
        UserRole.INSURANCE_OFFICER,
        UserRole.COOPERATIVE_OFFICER,
    }
)


def ensure_farmer_access(user: User, farmer: Farmer) -> None:
    """Only the farmer who owns a record, or staff, may touch it.

    Checking "is this a farmer, and is it theirs" is not enough on its own: that
    leaves every other signed-in role - a buyer, say - able to read, change and
    delete any farmer's farm, crop and activity. So the roles that are allowed to
    work across farmers are named explicitly, and anything else is refused.
    """
    if farmer.user_id == user.id:
        return
    if user.role in CROSS_FARM_ROLES:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_your_record")


def get_language_dep(request: Request) -> str:
    negotiate_language(request)
    return get_language()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
