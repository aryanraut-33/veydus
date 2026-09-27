# ─────────────────────────────────────────────────────────────────
# VEYDUS — GCP Identity Platform Token Verification [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Cryptographic signature and claims verification for JWTs issued by
#        GCP Identity Platform (Firebase Auth) via Google's public JWKS endpoint.
# How:   - PyJWKClient: Fetches and caches Google's public RSA signing keys.
#        - Validates RS256 signature, expiry, audience (GCP Project ID), and issuer.
#        - Extracts and returns `idp_subject` (the authenticated user's Identity Platform UID).
#        - [DEFECT PREVENTION HLD §6.2]: The token carries `idp_subject` ONLY.
#          `org_id`, `department_id`, and `hierarchy_level` are NEVER extracted or
#          trusted from JWT claims. Any attempt to read scope from tokens is a defect.
#        - Provides dev fallback (HS256) when configured for hermetic local testing.
# Why:   HLD §6.2 mandate: Token verification only proves caller identity (idp_subject).
#        All tenant authorization attributes MUST be resolved server-side from the
#        database to prevent token-forgery and privilege escalation attacks.
# Tools: pyjwt (jwt, PyJWKClient), cryptography, pydantic, logging.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import jwt
from jwt import PyJWKClient, PyJWTError

if TYPE_CHECKING:
    from veydus.config import Settings

logger = logging.getLogger(__name__)

# Google Identity Platform Public JWKS Endpoint
GOOGLE_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
_jwk_client: PyJWKClient | None = None


class TokenVerificationError(Exception):
    """Raised when an Identity Platform JWT is malformed, expired, or invalid."""


class ForbiddenScopeInTokenError(Exception):
    """Raised if any component attempts to parse authorization scope from token claims."""


def get_jwk_client() -> PyJWKClient:
    """Returns a singleton PyJWKClient instance with key caching."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, max_cached_keys=16)
    return _jwk_client


def verify_idp_token(token: str, settings: Settings) -> str:
    """Verifies a JWT and extracts the caller's idp_subject.

    Args:
        token: Raw Bearer JWT string.
        settings: Application settings containing project ID and auth mode.

    Returns:
        The verified `idp_subject` string (Firebase Auth UID).

    Raises:
        TokenVerificationError: If signature, audience, issuer, or expiry is invalid.
        ForbiddenScopeInTokenError: If code attempts to extract tenant scope from claims.
    """
    if not token:
        raise TokenVerificationError("Empty bearer token provided")

    # 1. Dev / Testing Fallback Mode (HMAC-SHA256)
    if settings.auth_mode == "dev":
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            idp_subject = payload.get("sub") or payload.get("idp_subject")
            if not idp_subject:
                raise TokenVerificationError("Missing 'sub' claim in dev token")
            return str(idp_subject)
        except PyJWTError as exc:
            raise TokenVerificationError(f"Invalid dev token: {exc}") from exc

    # 2. Production GCP Identity Platform RS256 Verification via Google JWKS
    project_id = settings.gcp_project_id
    if not project_id:
        raise TokenVerificationError(
            "GCP_PROJECT_ID is not configured for Identity Platform validation"
        )

    expected_issuer = f"https://securetoken.google.com/{project_id}"

    try:
        jwk_client = get_jwk_client()
        signing_key = jwk_client.get_signing_key_from_jwt(token)

        idp_payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=expected_issuer,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )
    except PyJWTError as exc:
        logger.warning("Identity Platform token verification failed: %s", exc)
        raise TokenVerificationError(f"Invalid Identity Platform token: {exc}") from exc

    # 3. Extract Identity Platform UID
    idp_subject = idp_payload.get("sub")
    if not idp_subject:
        raise TokenVerificationError("Identity Platform token is missing subject 'sub' claim")

    return str(idp_subject)


def extract_scope_claims_prohibited(claims: dict[str, Any]) -> None:
    """Enforces HLD §6.2: Defect check ensuring token claims are NEVER read for tenant scope.

    Raises ForbiddenScopeInTokenError if caller attempts to derive authorization
    scope (org_id, department_id, hierarchy_level) from token claims.
    """
    forbidden_scope_keys = {"org_id", "department_id", "hierarchy_level", "role"}
    found_keys = forbidden_scope_keys.intersection(claims.keys())
    if found_keys:
        raise ForbiddenScopeInTokenError(
            f"DEFECT [HLD §6.2]: Attempted to read authorization scope {found_keys} from token claims. "
            "Scope MUST be resolved server-side from the database."
        )
