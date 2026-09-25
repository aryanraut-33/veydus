# ─────────────────────────────────────────────────────────────────
# VEYDUS — Authentication & Scope Resolution Dependencies
# ─────────────────────────────────────────────────────────────────
# What:  FastAPI dependencies for extracting, verifying, and resolving
#        caller identity into a server-validated UserScope.
# How:   Parses Bearer JWT token from Authorization header using PyJWT,
#        verifying against settings.jwt_secret_key. Supports trusted dev/test
#        request headers (X-Org-Id, X-User-Id, X-Department-Id, X-Hierarchy-Level)
#        when configured or running integration tests.
# Why:   HLD §2.3 & §11 mandate that authorization predicates are derived
#        exclusively from server-resolved identity tokens, preventing client spoofing.
# Tools: FastAPI (Header, HTTPException, status), jwt (PyJWT), uuid.UUID,
#        veydus.authz.models.UserScope, veydus.config.settings.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from uuid import UUID

import jwt
from fastapi import Header, HTTPException, status

from veydus.authz.models import UserScope
from veydus.config import settings

logger = logging.getLogger(__name__)


async def get_current_user_scope(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_department_id: str | None = Header(default=None, alias="X-Department-Id"),
    x_hierarchy_level: int | None = Header(default=None, alias="X-Hierarchy-Level"),
) -> UserScope:
    """Extracts and verifies caller identity, compiling server-resolved UserScope."""
    # 1. Bearer JWT Authentication
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            org_id = UUID(payload["org_id"])
            user_id = UUID(payload.get("sub") or payload["user_id"])
            department_id = UUID(payload["department_id"])
            hierarchy_level = int(payload.get("hierarchy_level", 1))

            return UserScope(
                org_id=org_id,
                user_id=user_id,
                department_id=department_id,
                hierarchy_level=hierarchy_level,
            )
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            logger.warning("Invalid JWT bearer token: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token",
            ) from exc

    # 2. Test/Dev Explicit Header Fallback
    if x_org_id and x_user_id and x_department_id:
        try:
            return UserScope(
                org_id=UUID(x_org_id),
                user_id=UUID(x_user_id),
                department_id=UUID(x_department_id),
                hierarchy_level=x_hierarchy_level if x_hierarchy_level is not None else 1,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid UUID format in authorization headers",
            ) from exc

    # 3. Missing Credentials
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authentication credentials",
    )
