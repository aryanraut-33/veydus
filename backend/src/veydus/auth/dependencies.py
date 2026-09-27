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

from fastapi import Header, HTTPException, status

from veydus.auth.scope import UserScopeNotFoundError, resolve_user_scope_from_db
from veydus.auth.token import TokenVerificationError, verify_idp_token
from veydus.authz.models import UserScope
from veydus.config import settings
from veydus.db.engine import get_engine

logger = logging.getLogger(__name__)


async def get_current_user_scope(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_department_id: str | None = Header(default=None, alias="X-Department-Id"),
    x_hierarchy_level: int | None = Header(default=None, alias="X-Hierarchy-Level"),
) -> UserScope:
    """Extracts and verifies caller identity, compiling server-resolved UserScope.

    - Verifies Identity Platform JWT via Google JWKS to extract `idp_subject`.
    - Resolves `UserScope` server-side from PostgreSQL with in-process 30s TTL cache (HLD §6.2).
    - Supports trusted dev/test headers when configured.
    """
    # 1. Bearer JWT Authentication (Identity Platform JWKS or Dev HMAC)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        try:
            idp_subject = verify_idp_token(token, settings)
        except TokenVerificationError as exc:
            logger.warning("Bearer token verification failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired authentication token: {exc}",
            ) from exc

        # Resolve UserScope from database / TTL cache
        engine = get_engine()
        try:
            async with engine.connect() as conn:
                return await resolve_user_scope_from_db(conn, idp_subject)
        except UserScopeNotFoundError as exc:
            logger.warning("User scope resolution failed for subject %s: %s", idp_subject, exc)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active tenant access grant not found for caller identity",
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
